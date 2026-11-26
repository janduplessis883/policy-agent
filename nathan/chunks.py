"""Split a policy into chunks for the search index.

One Markdown section is one chunk. Tiny sections are merged into a neighbour so
the index is not full of headings with no procedure. A section longer than about
500 words is cut into 400-word pieces with a 40-word overlap. Each piece keeps
the policy title and the section heading, which is also what gets embedded.
"""

import hashlib
from dataclasses import dataclass

from nathan.passages import split_sections

MAX_WORDS = 500
TARGET_WORDS = 400
OVERLAP_WORDS = 40
MIN_WORDS = 40
# text-embedding-3-small accepts 8192 tokens. Two characters per token stays under that
# for dense tables and pasted text, which can be far longer than their word count.
EMBED_TOKEN_LIMIT = 7000
CHARS_PER_TOKEN = 2


@dataclass(frozen=True)
class PolicyChunk:
    title: str
    heading: str
    text: str
    chunk_index: int
    embed_text: str
    content_hash: str


def estimate_tokens(text: str) -> int:
    """Conservative token estimate. Dense text is about one token per two characters."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def clean_text(text: str) -> str:
    """Drop NUL bytes and lone surrogates. Both make an embeddings request return 400."""
    if not isinstance(text, str):
        return ""
    return "".join(char for char in text.replace("\x00", "") if not 0xD800 <= ord(char) <= 0xDFFF)


def usable_text(text: str) -> str:
    return clean_text(text).strip()


def split_for_limit(text: str, max_tokens: int = EMBED_TOKEN_LIMIT) -> list[str]:
    """Split text so each piece is within the embedding model's token limit."""
    text = usable_text(text)
    if not text:
        return []
    if estimate_tokens(text) <= max_tokens:
        return [text]
    max_chars = max(200, max_tokens * CHARS_PER_TOKEN)
    overlap = 80
    parts = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            split_at = text.rfind(" ", start + max_chars // 2, end)
            if split_at != -1:
                end = split_at
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
    budget = EMBED_TOKEN_LIMIT - estimate_tokens(prefix) - 2
    budget = max(budget, 200)
    return split_for_limit(body, budget)
