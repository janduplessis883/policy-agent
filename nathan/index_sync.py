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
    has_embedding: bool = True


@dataclass
class SyncPlan:
    delete_ids: list[str] = field(default_factory=list)
    refresh: list = field(default_factory=list)
    skip_ids: list[str] = field(default_factory=list)
    excluded: int = 0
    unembedded: int = 0


def plan_sync(
    policies, grouped: dict[str, list[StoredChunk]], model: str, dimensions: int
) -> SyncPlan:
    kept = [policy for policy in policies if not getattr(policy, "excluded", False)]
    kept_ids = {policy.id for policy in kept}
    delete_ids = [page_id for page_id in grouped if page_id not in kept_ids]
    refresh, skip_ids = [], []
    unembedded = 0
    for policy in kept:
        rows = grouped.get(policy.id, [])
        if _already_current(policy, rows, model, dimensions):
            skip_ids.append(policy.id)
        else:
            refresh.append(policy)
            if rows and any(not row.has_embedding for row in rows):
                unembedded += 1
    return SyncPlan(
        delete_ids=delete_ids,
        refresh=refresh,
        skip_ids=skip_ids,
        excluded=len(policies) - len(kept),
        unembedded=unembedded,
    )


def format_sync_report(inventory: int, plan: SyncPlan, counts: dict, errors: list[str]) -> str:
    """One sentence the Re-index button can show. A zero result says why."""
    waiting = max(len(plan.refresh) - counts.get("processed", 0), 0)
    noun = "page" if inventory == 1 else "pages"
    text = (
        f"Index update finished. Notion inventory: {inventory} {noun} "
        f"({plan.excluded} excluded as archived or draft status, "
        f"{len(plan.skip_ids)} already current). "
        f"Embedded {counts.get('embedded', 0)}, "
        f"stored without embeddings {counts.get('without_embeddings', 0)}, "
        f"metadata only {counts.get('metadata', 0)}, "
        f"removed {len(plan.delete_ids)}, "
        f"read failed {counts.get('read_error', 0)}."
    )
    if waiting:
        text += f" {waiting} changed pages are still waiting for a later pass."
    if plan.unembedded:
        noun_again = "page" if plan.unembedded == 1 else "pages"
        verb = "was" if plan.unembedded == 1 else "were"
        text += f" {plan.unembedded} {noun_again} had no embedding and {verb} included again."
    if inventory == 0:
        text += " The inventory query returned no policy pages."
    if counts.get("without_embeddings"):
        text += " Embedding failed; full-text search can still use the stored text."
    if errors:
        text += " Details: " + "; ".join(errors[:5])
        if len(errors) > 5:
            text += f"; and {len(errors) - 5} more."
    return text


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
        if not row.has_embedding:
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
        and row.has_embedding
        for row in rows
    )
