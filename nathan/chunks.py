"""Split a policy into chunks for the search index.

One Markdown section is one chunk. Tiny sections are merged into a neighbour so
the index is not full of headings with no procedure. A section longer than about
500 words is cut into 400-word pieces with a 40-word overlap. Each piece keeps
the policy title and the section heading, which is also what gets embedded.
"""

import hashlib
import logging
from dataclasses import dataclass

from nathan.passages import split_sections

logger = logging.getLogger(__name__)

MAX_WORDS = 500
TARGET_WORDS = 400
OVERLAP_WORDS = 40
MIN_WORDS = 40
# text-embedding-3-small rejects inputs over 8192 tokens. Stay under that.
EMBED_TOKEN_LIMIT = 8000
CHARS_PER_TOKEN = 2
_TOKEN_OVERLAP = 50
_encoder = None
_encoder_failed = False


@dataclass(frozen=True)
class PolicyChunk:
    title: str
    heading: str
    text: str
    chunk_index: int
    embed_text: str
    content_hash: str


def _get_encoder():
    """cl100k_base, the encoding used by text-embedding-3-small. None if unavailable."""
    global _encoder, _encoder_failed
    if _encoder_failed:
        return None
    if _encoder is not None:
        return _encoder
    try:
        import tiktoken

        _encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 -- missing encoder must not stop indexing
        logger.warning(
            "tiktoken cl100k_base is unavailable; estimating tokens at 2 characters each"
        )
        _encoder_failed = True
        return None
    return _encoder


def count_tokens(text: str) -> int:
    """Exact cl100k_base count, or about 2 characters per token if that encoder is missing."""
    if not text:
        return 0
    encoder = _get_encoder()
    if encoder is None:
        return max(1, len(text) // CHARS_PER_TOKEN)
    try:
        return len(encoder.encode(text))
    except Exception:  # noqa: BLE001 -- a bad string still gets a size estimate
        logger.warning("Token count failed; estimating at 2 characters per token")
        return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_tokens(text: str) -> int:
    """Token count used to keep embedding inputs under the model limit."""
    return count_tokens(text)


def clean_text(text: str) -> str:
    """Drop NUL bytes and lone surrogates. Both make an embeddings request return 400."""
    if not isinstance(text, str):
        return ""
    return "".join(char for char in text.replace("\x00", "") if not 0xD800 <= ord(char) <= 0xDFFF)


def usable_text(text: str) -> str:
    return clean_text(text).strip()


def split_for_limit(text: str, max_tokens: int = EMBED_TOKEN_LIMIT) -> list[str]:
    """Split text on token boundaries so each piece stays under the embedding limit."""
    text = usable_text(text)
    if not text:
        return []
    if count_tokens(text) <= max_tokens:
        return [text]
    encoder = _get_encoder()
    if encoder is None:
        return _split_by_characters(text, max_tokens)
    try:
        tokens = encoder.encode(text)
    except Exception:  # noqa: BLE001 -- fall back to a character split
        return _split_by_characters(text, max_tokens)
    parts = []
    start = 0
    while start < len(tokens):
        end = min(len(tokens), start + max_tokens)
        piece = encoder.decode(tokens[start:end]).strip()
        while piece and count_tokens(piece) > max_tokens and end - start > 1:
            end -= max(1, (end - start) // 10)
            piece = encoder.decode(tokens[start:end]).strip()
        if piece:
            parts.append(piece)
        if end >= len(tokens):
            break
        start = max(end - _TOKEN_OVERLAP, start + 1)
    return parts


def split_in_half(text: str) -> tuple[str, str]:
    """Cut text in half on a token boundary. Used when the provider still says it is too long."""
    text = usable_text(text)
    if len(text) < 2:
        return text, ""
    encoder = _get_encoder()
    if encoder is not None:
        try:
            tokens = encoder.encode(text)
        except Exception:  # noqa: BLE001 -- fall back to a character split
            tokens = []
        if len(tokens) >= 2:
            mid = len(tokens) // 2
            left = encoder.decode(tokens[:mid]).strip()
            right = encoder.decode(tokens[mid:]).strip()
            if left and right and left != text and right != text:
                return left, right
    mid = max(1, len(text) // 2)
    return text[:mid].strip(), text[mid:].strip()


def _split_by_characters(text: str, max_tokens: int) -> list[str]:
    """Fallback when cl100k_base is unavailable. About 2 characters per token."""
    max_chars = max(200, max_tokens * CHARS_PER_TOKEN)
    overlap = 80
    parts = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


def chunk_policy(title: str, markdown: str) -> list[PolicyChunk]:
    sections = _merge_tiny(split_sections(clean_text(markdown or "")))
    pieces: list[tuple[str, str]] = []
    safe_title = usable_text(title) if isinstance(title, str) else ""
    for heading, body in sections:
        for part in _split_long(body):
            for fitted in _fit_embed_text(safe_title, heading, part):
                pieces.append((heading, fitted))
    chunks = []
    for index, (heading, text) in enumerate(pieces):
        embed_text = f"{safe_title}\n{heading}\n{text}".strip()
        chunks.append(
            PolicyChunk(
                title=safe_title,
                heading=heading or "",
                text=text,
                chunk_index=index,
                embed_text=embed_text,
                content_hash=hashlib.sha256(embed_text.encode("utf-8")).hexdigest(),
            )
        )
    return chunks


def _merge_tiny(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    for heading, body in sections:
        body = (body or "").strip()
        if not body:
            continue
        if merged and _word_count(body) < MIN_WORDS:
            previous_heading, previous_body = merged[-1]
            extra_heading = f"\n\n## {heading}\n" if heading else "\n\n"
            merged[-1] = (previous_heading or heading, f"{previous_body}{extra_heading}{body}")
            continue
        if merged and _word_count(merged[-1][1]) < MIN_WORDS:
            previous_heading, previous_body = merged[-1]
            label = f"## {previous_heading}\n" if previous_heading else ""
            merged[-1] = (heading or previous_heading, f"{label}{previous_body}\n\n{body}")
            continue
        merged.append((heading, body))
    return merged


def _split_long(text: str) -> list[str]:
    words = text.split()
    if len(words) <= MAX_WORDS:
        return [text] if text.strip() else []
    parts = []
    start = 0
    while start < len(words):
        end = min(start + TARGET_WORDS, len(words))
        if 0 < len(words) - end < MIN_WORDS:
            end = len(words)
        parts.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = max(end - OVERLAP_WORDS, start + 1)
    return parts


def _word_count(text: str) -> int:
    return len(text.split())


def _fit_embed_text(title: str, heading: str, body: str) -> list[str]:
    """Return body pieces whose title, heading and body stay within the token limit."""
    body = usable_text(body)
    if not body:
        return []
    prefix = f"{title}\n{heading}\n"
    budget = max(200, EMBED_TOKEN_LIMIT - count_tokens(prefix))
    pieces = split_for_limit(body, budget)
    fitted = []
    for piece in pieces:
        if count_tokens(f"{prefix}{piece}") <= EMBED_TOKEN_LIMIT:
            fitted.append(piece)
            continue
        fitted.extend(split_for_limit(piece, max(200, budget // 2)))
    return fitted
