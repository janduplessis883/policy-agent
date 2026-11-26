"""Embeddings from OpenRouter. Chat completions stay on FreeLLMAPI.

The public catalogue is GET https://openrouter.ai/api/v1/embeddings/models.
openai/text-embedding-3-small is on that list and returns 1536 numbers, which
matches the policy_chunks vector column.
"""

import logging
import re
import time

import httpx

from nathan.chunks import clean_text, estimate_tokens, split_for_limit
from nathan.index_sync import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
_BATCH_SIZE = 16
_MAX_BATCH_TOKENS = 8000
_ATTEMPTS = 4
_RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)


class Embedder:
    """OpenAI-compatible embeddings at OpenRouter, shared by indexing and search."""

    def __init__(self, settings, client=None, sleep=None, batch_size=_BATCH_SIZE):
        self.model = settings.embedding_model or DEFAULT_EMBEDDING_MODEL
        self.dimensions = EMBEDDING_DIMENSIONS
        self.last_error = ""
        self._errors: list[str] = []
        self._status = 0
        self._key = (settings.openrouter_api_key or "").strip()
        self._sleep = sleep or time.sleep
        self._batch_size = batch_size
        self._client = client or httpx.Client(timeout=60, follow_redirects=False)
        if not self._key:
            logger.warning(
                "OPENROUTER_API_KEY is not set. Vector search is off; full-text search still runs."
            )

    def embed(self, texts: list[str]) -> list[list[float] | None] | None:
        """One vector per input. A blank or rejected input is None; other inputs still embed."""
        self.last_error = ""
        self._errors = []
        self._status = 0
        if not texts:
            return []
        if not self._key:
            self.last_error = "OPENROUTER_API_KEY is not set"
            return None
        prepared = [split_for_limit(clean_text(text or "")) for text in texts]
        jobs = [(index, piece) for index, pieces in enumerate(prepared) for piece in pieces]
        embedded = self._embed_jobs(jobs)
        if embedded is None:
            return None
        cursor = 0
        results: list[list[float] | None] = []
        for pieces in prepared:
            if not pieces:
                results.append(None)
                continue
            group = embedded[cursor : cursor + len(pieces)]
            cursor += len(pieces)
            results.append(None if any(vector is None for vector in group) else _average(group))
        self.last_error = "; ".join(self._errors)[:500]
        return results

    def embed_one(self, text: str) -> list[float] | None:
        vectors = self.embed([text])
        if not vectors:
            return None
        return vectors[0]

    def _embed_jobs(self, jobs: list[tuple[int, str]]) -> list[list[float] | None] | None:
        results: list[list[float] | None] = [None] * len(jobs)
        numbered = list(enumerate(jobs))
        for batch in _batches(numbered, self._batch_size, _MAX_BATCH_TOKENS):
            vectors = self._embed_batch([piece for _, (_, piece) in batch])
            if vectors is None and self._status == 400 and len(batch) > 1:
                for job_index, (_, piece) in batch:
                    one = self._embed_batch([piece])
                    if one is None:
                        self._note_error(self.last_error)
                    else:
                        results[job_index] = one[0]
                continue
            if vectors is None and self._status == 400:
                self._note_error(self.last_error)
                continue
            if vectors is None:
                return None
            for (job_index, _), vector in zip(batch, vectors):
                results[job_index] = vector
        return results

    def _note_error(self, message: str):
        if message and message not in self._errors:
            self._errors.append(message)

    def _embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        self._status = 0
        body = self._post(
            {
                "model": self.model,
                "input": texts,
                "dimensions": self.dimensions,
                "encoding_format": "float",
            }
        )
        if body is None:
            return None
        data = body.get("data")
        if not isinstance(data, list):
            self.last_error = "response had no embeddings"
            logger.warning("OpenRouter embedding response had no embeddings")
            return None
        ordered = sorted(
            (item for item in data if isinstance(item, dict)),
            key=lambda item: item.get("index") if isinstance(item.get("index"), int) else 0,
        )
        vectors = []
        for item in ordered:
            vector = item.get("embedding")
            if not isinstance(vector, list):
                self.last_error = "embedding was not a list of numbers"
                logger.warning("OpenRouter embedding was not a list of numbers")
                return None
            try:
                vectors.append([float(value) for value in vector])
            except (TypeError, ValueError):
                self.last_error = "embedding was not a list of numbers"
                logger.warning("OpenRouter embedding was not a list of numbers")
                return None
        if len(vectors) != len(texts) or any(len(vector) != self.dimensions for vector in vectors):
            self.last_error = f"{self.model} returned a dimension other than {self.dimensions}"
            logger.warning(
                "Embedding model %s did not return %s dimensions", self.model, self.dimensions
            )
            return None
        return vectors

    def _post(self, payload: dict) -> dict | None:
        delay = 1.0
        last_status = 0
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                response = self._client.post(
                    OPENROUTER_EMBEDDINGS_URL, json=payload, headers=self._headers()
                )
            except httpx.TimeoutException:
                self.last_error = "timeout"
                logger.warning("OpenRouter embedding timed out (attempt %s)", attempt)
                if attempt == _ATTEMPTS:
                    return None
                self._sleep(delay)
                delay = min(delay * 2, 30)
                continue
            except httpx.HTTPError as exc:
                self.last_error = type(exc).__name__
                logger.warning("OpenRouter embedding request failed (%s)", self.last_error)
                if attempt == _ATTEMPTS:
                    return None
                self._sleep(delay)
                delay = min(delay * 2, 30)
                continue
            last_status = response.status_code
            if response.status_code in _RETRY_STATUSES and attempt < _ATTEMPTS:
                wait = _retry_delay(response, delay)
                logger.warning("OpenRouter embedding HTTP %s; retrying", response.status_code)
                self._sleep(wait)
                delay = min(delay * 2, 30)
                continue
            if response.is_error:
                self._status = response.status_code
                self.last_error = _error_summary(response, self._key)
                logger.warning("OpenRouter embedding %s", self.last_error)
                return None
            try:
                body = response.json()
            except ValueError:
                self.last_error = "invalid JSON"
                logger.warning("OpenRouter embedding returned invalid JSON")
                return None
            if not isinstance(body, dict):
                self.last_error = "unexpected response"
                logger.warning("OpenRouter embedding returned an unexpected response")
                return None
            return body
        self.last_error = f"HTTP {last_status} after retries"
        logger.warning("OpenRouter embedding gave up after HTTP %s", last_status)
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/janduplessis883/policy-agent",
            "X-Title": "Nathan",
            "X-OpenRouter-Title": "Nathan",
            "X-OpenRouter-App-Visibility": "hidden",
        }


def _batches(jobs, max_items: int, max_tokens: int):
    batch, tokens = [], 0
    for job in jobs:
        piece = job[1][1]
        cost = estimate_tokens(piece)
        if batch and (len(batch) >= max_items or tokens + cost > max_tokens):
            yield batch
            batch, tokens = [], 0
        batch.append(job)
        tokens += cost
    if batch:
        yield batch


def _average(vectors: list[list[float]]) -> list[float]:
    if len(vectors) == 1:
        return vectors[0]
    count = float(len(vectors))
    return [sum(item[index] for item in vectors) / count for index in range(len(vectors[0]))]


def _retry_delay(response, fallback: float) -> float:
    raw = (response.headers.get("retry-after") or "").strip()
    try:
        delay = float(raw)
    except ValueError:
        delay = fallback
    return min(max(delay, 0), 30)


def _error_summary(response, key: str) -> str:
    """HTTP status plus OpenRouter's code and message, with the key removed."""
    code, message = "", ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            raw_code = error.get("code")
            if raw_code not in (None, ""):
                code = str(raw_code)
            if isinstance(error.get("message"), str):
                message = error["message"]
        elif isinstance(error, str):
            message = error
        elif isinstance(payload.get("message"), str):
            message = payload["message"]
    if not message:
        message = getattr(response, "text", "") or ""
    message = _redact(message, key)[:300]
    code = _redact(code, key)[:80]
    if code and message:
        return f"HTTP {response.status_code}: {code}: {message}"
    if message:
        return f"HTTP {response.status_code}: {message}"
    return f"HTTP {response.status_code}"


def _redact(text: str, key: str) -> str:
    if key:
        text = text.replace(key, "[redacted]")
    return _BEARER.sub("Bearer [redacted]", text)
