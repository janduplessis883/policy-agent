"""Supabase REST client for the policy chunk table.

The key is sent only as a header. It is never written to a log line.
New sb_secret_ keys are not JWTs, so they go in the apikey header only.
A legacy service_role key is a JWT and is also sent as Authorization: Bearer.
"""

import base64
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)
_SECRET_IN_TEXT = re.compile(
    r"(?:Bearer\s+)\S+|sb_secret_[A-Za-z0-9_\-]+|sb_publishable_[A-Za-z0-9_\-]+|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
    re.IGNORECASE,
)


class IndexUnavailable(Exception):
    """The index could not be used. Callers fall back to keyword search."""


def is_legacy_jwt(key: str) -> bool:
    """True for the old anon and service_role keys, which are three-part JWTs."""
    parts = (key or "").split(".")
    return len(parts) == 3 and all(parts) and parts[0].startswith("eyJ")


def jwt_role(key: str) -> str:
    """Role claim inside a legacy JWT, or an empty string. The key itself is not logged."""
    if not is_legacy_jwt(key):
        return ""
    payload = key.split(".")[1]
    padding = "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
    except (ValueError, json.JSONDecodeError):
        return ""
    role = data.get("role") if isinstance(data, dict) else ""
    return role if isinstance(role, str) else ""


def supabase_headers(service_key: str) -> dict[str, str]:
    """Headers for PostgREST. A non-JWT secret must not be sent as a Bearer token."""
    key = (service_key or "").strip()
    headers = {"apikey": key, "Content-Type": "application/json"}
    lowered = key.lower()
    if lowered.startswith("sb_publishable_") or jwt_role(key) in {"anon", "authenticated"}:
        logger.warning(
            "SUPABASE_SERVICE_ROLE_KEY is a publishable or anon key. "
            "The index needs the secret key (sb_secret_...) or the legacy service_role JWT. "
            "A publishable key cannot bypass row level security."
        )
    if is_legacy_jwt(key):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _scrub(text: str) -> str:
    return _SECRET_IN_TEXT.sub("[redacted]", text or "")


def _postgrest_detail(response) -> str:
    """PostgREST code, message and hint, with any key material removed."""
    payload = None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        bits = []
        for field in ("code", "message", "hint", "details"):
            value = payload.get(field)
            if isinstance(value, str) and value.strip():
                bits.append(f"{field}={value.strip()}")
        if bits:
            return _scrub("; ".join(bits))[:500]
    return _scrub(getattr(response, "text", "") or "")[:500]


class SupabaseStore:
    def __init__(self, url: str, service_key: str, transport=None):
        self.url = url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.url + "/rest/v1/",
            timeout=30,
            headers=supabase_headers(service_key),
            transport=transport,
        )

    def close(self):
        self._client.close()

    def try_lock(self, holder: str, seconds: int = 300) -> bool:
        result = self._rpc("nathan_try_index_lock", {"p_holder": holder, "p_seconds": seconds})
        return bool(result)

    def unlock(self, holder: str):
        self._rpc("nathan_index_unlock", {"p_holder": holder})

    def fetch_state(self) -> list[dict]:
        rows = self._get_all(
            {
                "select": (
                    "page_id,chunk_index,content_hash,last_edited_time,"
                    "embedding_model,embedding_dimensions"
                ),
                "order": "page_id.asc,chunk_index.asc",
            }
        )
        missing = {
            (row.get("page_id"), int(row.get("chunk_index") or 0))
            for row in self._get_all({"select": "page_id,chunk_index", "embedding": "is.null"})
        }
        for row in rows:
            key = (row.get("page_id"), int(row.get("chunk_index") or 0))
            row["has_embedding"] = key not in missing
        return rows

    def _get_all(self, params: dict) -> list[dict]:
        rows, offset = [], 0
        while True:
            page = dict(params)
            page["limit"] = 1000
            page["offset"] = offset
            batch = self._get("policy_chunks", params=page)
            rows.extend(batch)
            if len(batch) < 1000:
                return rows
            offset += 1000

    def replace_page(self, page_id: str, chunks: list[dict]):
        self._rpc("replace_policy_chunks", {"p_page_id": page_id, "p_chunks": chunks})

    def touch_page(self, page_id: str, title: str, last_edited: str, is_live: bool):
        self._rpc(
            "touch_policy_page",
            {
                "p_page_id": page_id,
                "p_title": title,
                "p_last_edited": last_edited,
                "p_is_live": is_live,
            },
        )

    def delete_pages(self, page_ids: list[str]):
        if page_ids:
            self._rpc("delete_policy_pages", {"p_page_ids": page_ids})

    def search_fts(self, query_text: str, match_count: int, live_only: bool) -> list[dict]:
        return self._rpc(
            "search_policy_chunks_fts",
            {"query_text": query_text, "match_count": match_count, "live_only": live_only},
        )

    def search_vector(
        self, embedding: list[float], match_count: int, live_only: bool, embedding_model: str = ""
    ) -> list[dict]:
        literal = "[" + ",".join(f"{value:.6f}" for value in embedding) + "]"
        payload = {
            "query_embedding": literal,
            "match_count": match_count,
            "live_only": live_only,
        }
        if embedding_model:
            payload["p_embedding_model"] = embedding_model
        return self._rpc("search_policy_chunks_vector", payload)

    def neighbours(self, page_id: str, chunk_index: int) -> list[dict]:
        return self._rpc(
            "neighbour_policy_chunks",
            {"p_page_id": page_id, "p_index": chunk_index},
        )

    def _get(self, path: str, params: dict) -> list:
        response = self._request("GET", path, params=params)
        if not isinstance(response, list):
            raise IndexUnavailable("Supabase returned an unexpected list")
        return response

    def _rpc(self, name: str, payload: dict):
        return self._request("POST", f"rpc/{name}", json=payload)

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("Policy index request failed (%s)", type(exc).__name__)
            raise IndexUnavailable("Policy index is unavailable") from exc
        if response.is_error:
            detail = _postgrest_detail(response)
            logger.warning(
                "Policy index HTTP %s on %s: %s",
                response.status_code,
                path.split("?")[0],
                detail or "no error body",
            )
            raise IndexUnavailable(f"Policy index returned HTTP {response.status_code}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            logger.warning("Policy index returned invalid JSON")
            raise IndexUnavailable("Policy index returned invalid JSON") from exc
