"""Optional policy-text index. Missing Supabase settings leave this disabled."""

import logging
import threading
import time
import uuid
from dataclasses import dataclass

from nathan.chunks import chunk_policy
from nathan.fusion import reciprocal_rank_fusion
from nathan.index_sync import (
    EMBEDDING_DIMENSIONS,
    StoredChunk,
    plan_sync,
    same_content,
)
from nathan.supabase_store import IndexUnavailable, SupabaseStore

logger = logging.getLogger(__name__)

_BACKGROUND_INTERVAL = 600
_BACKGROUND_PAGES = 2


@dataclass(frozen=True)
class ChunkHit:
    page_id: str
    title: str
    heading: str
    text: str
    chunk_index: int
    is_live: bool


class NullIndex:
    """Keyword mode: title and tag search only."""

    enabled = False
    degraded = False

    def search(self, question, keyword_ids=(), live_only=True):
        return []

    def sync_incremental(self, max_pages=None):
        return "Index is not configured."

    def start_background(self):
        return None


class Embedder:
    """Embeddings from the same OpenAI-compatible endpoint as the chat model."""

    def __init__(self, settings):
        from openai import OpenAI

        self.model = settings.embedding_model
        self.dimensions = EMBEDDING_DIMENSIONS
        self._client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=60,
            max_retries=0,
        )

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(model=self.model, input=texts)
        except Exception as exc:  # noqa: BLE001 -- do not log the request; it can carry the key
            logger.warning("Embedding request failed (%s)", type(exc).__name__)
            return None
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in ordered]
        if any(len(vector) != self.dimensions for vector in vectors):
            logger.warning(
                "Embedding model %s did not return %s dimensions", self.model, self.dimensions
            )
            return None
        return vectors

    def embed_one(self, text: str) -> list[float] | None:
        vectors = self.embed([text])
        if not vectors:
            return None
        return vectors[0]


class PolicyIndex:
    def __init__(self, settings, notion):
        self.enabled = True
        self.degraded = False
        self._settings = settings
        self._notion = notion
        self._store = SupabaseStore(settings.supabase_url, settings.supabase_key)
        self._embedder = Embedder(settings)
        self._holder = f"nathan-{uuid.uuid4()}"
        self._background_started = False

    def search(self, question, keyword_ids=(), live_only=True) -> list[ChunkHit]:
        # Live answers do not retrieve non-live chunks. Gap and review passes ask for both.
        embedding = self._embedder.embed_one(question)
        vector_rows = (
            self._store.search_vector(embedding, 30, live_only) if embedding is not None else []
        )
        fts_rows = self._store.search_fts(question, 30, live_only)
        if not isinstance(vector_rows, list):
            vector_rows = []
        if not isinstance(fts_rows, list):
            fts_rows = []
        fused = reciprocal_rank_fusion(
            [
                _page_order(vector_rows),
                _page_order(fts_rows),
                [page_id for page_id in keyword_ids if page_id],
            ]
        )
        if not fused:
            return []
        chosen = fused[:20]
        hits = [_hit(row) for row in vector_rows + fts_rows if row.get("page_id") in set(chosen)]
        hits = _with_neighbours(self._store, hits)
        return _ordered(hits, chosen)

    def sync_incremental(self, max_pages=None) -> str:
        from nathan.tools.list_policies import list_policies
        from nathan.tools.read_policy import read_policy

        if not self._store.try_lock(self._holder):
            return "Another index update is already running."
        try:
            inventory = list_policies(self._notion)
            grouped = _group(self._store.fetch_state())
            plan = plan_sync(inventory, grouped, self._embedder.model, self._embedder.dimensions)
            self._store.delete_pages(plan.delete_ids)
            from nathan.tools.current_date import current_date

            today = current_date()
            refreshed = metadata = 0
            for policy in plan.refresh:
                if max_pages is not None and refreshed + metadata >= max_pages:
                    break
                outcome = _store_page(self, policy, grouped.get(policy.id, []), read_policy, today)
                if outcome == "embedded":
                    refreshed += 1
                elif outcome == "metadata":
                    metadata += 1
            self.degraded = False
            return (
                f"Index update finished. Refreshed {refreshed}, "
                f"metadata only {metadata}, removed {len(plan.delete_ids)}, "
                f"already current {len(plan.skip_ids)}."
            )
        except IndexUnavailable:
            self.degraded = True
            raise
        finally:
            try:
                self._store.unlock(self._holder)
            except IndexUnavailable:
                logger.warning("Policy index lock was not released")

    def start_background(self):
        # One daemon for the process. Chat requests do not wait for embedding.
        if self._background_started:
            return
        self._background_started = True

        def loop():
            while True:
                try:
                    self.sync_incremental(max_pages=_BACKGROUND_PAGES)
                except Exception:
                    logger.exception("Background policy index sync failed")
                    self.degraded = True
                time.sleep(_BACKGROUND_INTERVAL)

        threading.Thread(target=loop, name="nathan-index-sync", daemon=True).start()


def build_index(settings, notion):
    if not getattr(settings, "index_enabled", False):
        logger.warning(
            "Policy index disabled; using keyword mode. "
            "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to turn it on."
        )
        return NullIndex()
    logger.warning("Policy index enabled")
    return PolicyIndex(settings, notion)


def _store_page(index: PolicyIndex, policy, stored: list[StoredChunk], read_policy, today) -> str:
    """Return embedded, metadata, or skipped. A failed read or embedding is retried next time."""
    try:
        document = read_policy(index._notion, policy)
    except Exception as exc:  # noqa: BLE001 -- keep indexing the other pages
        logger.warning("Could not read page %s for indexing (%s)", policy.id, type(exc).__name__)
        return "skipped"
    chunks = chunk_policy(policy.title or document.title, document.text)
    if not chunks:
        index._store.delete_pages([policy.id])
        return "metadata"
    model, dims = index._embedder.model, index._embedder.dimensions
    is_live = policy.is_live(today)
    if same_content(chunks, stored, model, dims):
        index._store.touch_page(
            policy.id, policy.title or "", policy.last_edited_time or "", is_live
        )
        return "metadata"
    vectors = index._embedder.embed([chunk.embed_text for chunk in chunks])
    if vectors is None or len(vectors) != len(chunks):
        logger.warning("Skipped re-embedding page %s", policy.id)
        return "skipped"
    payload = []
    for chunk, vector in zip(chunks, vectors):
        payload.append(
            {
                "title": chunk.title,
                "section_heading": chunk.heading,
                "chunk_index": chunk.chunk_index,
                "chunk_text": chunk.text,
                "content_hash": chunk.content_hash,
                "last_edited_time": policy.last_edited_time or "",
                "is_live": is_live,
                "embedding": "[" + ",".join(f"{value:.6f}" for value in vector) + "]",
                "embedding_model": model,
                "embedding_dimensions": dims,
            }
        )
    index._store.replace_page(policy.id, payload)
    return "embedded"


def _group(rows: list[dict]) -> dict[str, list[StoredChunk]]:
    grouped: dict[str, list[StoredChunk]] = {}
    for row in rows:
        page_id = row.get("page_id")
        if not page_id:
            continue
        grouped.setdefault(page_id, []).append(
            StoredChunk(
                chunk_index=int(row.get("chunk_index") or 0),
                content_hash=row.get("content_hash") or "",
                last_edited_time=row.get("last_edited_time"),
                embedding_model=row.get("embedding_model"),
                embedding_dimensions=row.get("embedding_dimensions"),
            )
        )
    return grouped


def _page_order(rows: list[dict]) -> list[str]:
    order = []
    for row in rows:
        page_id = row.get("page_id")
        if page_id and page_id not in order:
            order.append(page_id)
    return order


def _hit(row: dict) -> ChunkHit:
    return ChunkHit(
        page_id=row.get("page_id") or "",
        title=row.get("title") or "",
        heading=row.get("section_heading") or "",
        text=row.get("chunk_text") or "",
        chunk_index=int(row.get("chunk_index") or 0),
        is_live=bool(row.get("is_live")),
    )


def _with_neighbours(store: SupabaseStore, hits: list[ChunkHit]) -> list[ChunkHit]:
    # chunk_index lets us pull the previous and next piece of a long section.
    seen = {(hit.page_id, hit.chunk_index) for hit in hits}
    extra = []
    for hit in hits[:8]:
        try:
            rows = store.neighbours(hit.page_id, hit.chunk_index)
        except IndexUnavailable:
            logger.warning("Neighbour lookup failed; using the matched chunks only")
            return hits
        for row in rows or []:
            key = (row.get("page_id"), int(row.get("chunk_index") or 0))
            if key in seen:
                continue
            seen.add(key)
            extra.append(_hit(row))
    return hits + extra


def documents_from_hits(hits: list[ChunkHit], by_id, today, withhold_non_live: bool):
    """Group matched chunks into one document per policy, in fusion order.

    Neighbouring chunks are included so a step that starts in the previous piece
    is still visible. Non-live procedure text is withheld for ordinary answers.
    """
    from dataclasses import replace

    from nathan.domain import Document

    grouped: dict[str, list[ChunkHit]] = {}
    order = []
    for hit in hits:
        if hit.page_id not in by_id:
            continue
        if hit.page_id not in grouped:
            order.append(hit.page_id)
        grouped.setdefault(hit.page_id, []).append(hit)
    documents = []
    for page_id in order[:30]:
        policy = by_id[page_id]
        pieces = []
        seen = set()
        for hit in sorted(grouped[page_id], key=lambda item: item.chunk_index):
            if hit.chunk_index in seen:
                continue
            seen.add(hit.chunk_index)
            heading = hit.heading or "Section"
            pieces.append(f"# {heading}\n{hit.text}")
        text = "\n\n".join(pieces)
        document = Document(
            page_id,
            policy.title or hit_title(grouped[page_id]),
            text,
            complete=False,
            public_url=policy.public_url,
            indexed=True,
        )
        if withhold_non_live and not policy.is_live(today):
            document = replace(
                document,
                text="Not Live: procedure text withheld from operational answers.",
            )
        documents.append(document)
    return documents


def hit_title(hits: list[ChunkHit]) -> str:
    return next((hit.title for hit in hits if hit.title), "Untitled policy")


def _ordered(hits: list[ChunkHit], policy_order: list[str]) -> list[ChunkHit]:
    rank = {page_id: index for index, page_id in enumerate(policy_order)}
    unique = {}
    for hit in hits:
        unique.setdefault((hit.page_id, hit.chunk_index), hit)
    return sorted(
        unique.values(),
        key=lambda hit: (rank.get(hit.page_id, 10**6), hit.chunk_index),
    )
