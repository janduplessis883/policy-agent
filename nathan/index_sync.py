"""Decide which Notion pages the index needs to refresh.

A page is skipped when its last_edited_time and embedding model already match
the stored chunks. If Notion touches a page without changing the words, the
content hash stops a second embedding call. Pages missing from the inventory,
and archived or trashed pages, are deleted.
"""

from dataclasses import dataclass, field

from nathan.chunks import PolicyChunk

EMBEDDING_DIMENSIONS = 1536


@dataclass
class StoredChunk:
    chunk_index: int
    content_hash: str
    last_edited_time: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None


@dataclass
class SyncPlan:
    delete_ids: list[str] = field(default_factory=list)
    refresh: list = field(default_factory=list)
    skip_ids: list[str] = field(default_factory=list)


def plan_sync(
    policies, grouped: dict[str, list[StoredChunk]], model: str, dimensions: int
) -> SyncPlan:
    kept = [policy for policy in policies if not getattr(policy, "excluded", False)]
    kept_ids = {policy.id for policy in kept}
    delete_ids = [page_id for page_id in grouped if page_id not in kept_ids]
    refresh, skip_ids = [], []
    for policy in kept:
        rows = grouped.get(policy.id, [])
        if _already_current(policy, rows, model, dimensions):
            skip_ids.append(policy.id)
        else:
            refresh.append(policy)
    return SyncPlan(delete_ids=delete_ids, refresh=refresh, skip_ids=skip_ids)


def same_content(
    chunks: list[PolicyChunk], stored: list[StoredChunk], model: str, dimensions: int
) -> bool:
    """True when the words and the embedding model are unchanged, so do not re-embed."""
    if not chunks or len(chunks) != len(stored):
        return False
    by_index = {row.chunk_index: row for row in stored}
    for chunk in chunks:
        row = by_index.get(chunk.chunk_index)
        if row is None or row.content_hash != chunk.content_hash:
            return False
        if row.embedding_model != model or row.embedding_dimensions != dimensions:
            return False
    return True


def _already_current(policy, rows: list[StoredChunk], model: str, dimensions: int) -> bool:
    edited = getattr(policy, "last_edited_time", None) or ""
    if not edited or not rows:
        return False
    return all(
        (row.last_edited_time or "") == edited
        and row.embedding_model == model
        and row.embedding_dimensions == dimensions
        for row in rows
    )
