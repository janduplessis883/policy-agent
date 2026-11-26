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


@dataclass(frozen=True)
class PolicyChunk:
    title: str
    heading: str
    text: str
    chunk_index: int
    embed_text: str
    content_hash: str


def chunk_policy(title: str, markdown: str) -> list[PolicyChunk]:
    sections = _merge_tiny(split_sections(markdown or ""))
    pieces: list[tuple[str, str]] = []
    for heading, body in sections:
        for part in _split_long(body):
            pieces.append((heading, part))
    chunks = []
    safe_title = title.strip() if isinstance(title, str) else ""
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
