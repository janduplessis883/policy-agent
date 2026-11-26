"""Optional policy-text index. Missing Supabase settings leave this disabled."""

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from nathan.chunks import chunk_policy
from nathan.embeddings import Embedder
from nathan.fusion import fusion_scores
from nathan.index_sync import (
    StoredChunk,
    format_sync_report,
    plan_sync,
    same_content,
)
from nathan.progress import brief, emit
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
        # Embedding and full-text search use separate clients and do not depend on each
        # other. Overlap their network waits, while emitting all progress events here on
        # the calling thread so the Streamlit status panel remains reliable.
        def timed(call, *args):
            started = time.perf_counter()
            result = call(*args)
            return result, time.perf_counter() - started

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="nathan-search") as pool:
            embedding_future = pool.submit(timed, self._embedder.embed_one, question)
            fts_future = pool.submit(
                timed, self._store.search_fts, question, 30, live_only
            )
            embedding, embed_s = embedding_future.result()
            if embedding is None:
                reason = brief(
                    getattr(self._embedder, "last_error", "") or "no vector returned", 120
                )
                emit(
                    "Embedding query",
                    f"Query embedding skipped or failed: OpenRouter {self._embedder.model}, "
                    f"{embed_s:.2f}s. {reason}",
                )
            else:
                emit(
                    "Embedding query",
                    f"Query embedding: OpenRouter {self._embedder.model}, "
                    f"{len(embedding)} dimensions, {embed_s:.2f}s.",
                )
            vector_started = time.perf_counter()
            if embedding is not None:
                vector_rows = self._store.search_vector(
                    embedding, 30, live_only, self._embedder.model
                )
            else:
                vector_rows = []
            # Keep vector-query timing separate from the embedding request that now
            # overlaps full-text search.
            vector_s = time.perf_counter() - vector_started
            if not isinstance(vector_rows, list):
                vector_rows = []
            similarity = _top_similarity(vector_rows)
            similarity_text = (
                f"top similarity {similarity:.3f}"
                if similarity is not None
                else "similarity was not returned"
            )
            scope = "Live chunks only" if live_only else "Live and non-live chunks"
            emit(
                "Searching index: vector",
                f"Vector search: {len(vector_rows)} hits, {similarity_text}, {scope}, "
                f"{vector_s:.2f}s. Top: {_titles(vector_rows)}.",
            )
            fts_rows, fts_s = fts_future.result()
            if not isinstance(fts_rows, list):
                fts_rows = []
            emit(
                "Searching index: full-text",
                f"Full-text search: {len(fts_rows)} hits, {scope}, {fts_s:.2f}s. "
                f"Top: {_titles(fts_rows)}.",
            )
        keyword_ids = [page_id for page_id in keyword_ids if page_id]
        ranked = fusion_scores([_page_order(vector_rows), _page_order(fts_rows), keyword_ids])
        names = _title_by_id(vector_rows + fts_rows)
        top = ", ".join(
            f"{names.get(page_id, 'Matched policy')} {score:.3f}" for page_id, score in ranked[:5]
        )
        emit(
            "Fusing results",
            f"Fusion: {len(ranked)} policies. Title and tag list: {len(keyword_ids)}. "
            f"Top: {top or 'none'}.",
        )
        if not ranked:
            return []
        chosen = [page_id for page_id, _score in ranked[:20]]
        chosen_set = set(chosen)
        hits = [
            _hit(row)
            for row in vector_rows + fts_rows
            if row.get("page_id") in chosen_set
        ]
        before = {(hit.page_id, hit.chunk_index) for hit in hits}
        hits = _with_neighbours(self._store, hits)
        added = [hit for hit in hits if (hit.page_id, hit.chunk_index) not in before]
        added_titles = _unique([hit.title for hit in added if hit.title])
        emit(
            "Expanding neighbour chunks",
            f"Neighbour chunks: added {len(added)}"
            + (f" for {', '.join(added_titles[:5])}" if added_titles else "")
            + ".",
        )
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
            # None means every changed page. A background pass passes a small limit.
            counts = {
                "embedded": 0,
                "without_embeddings": 0,
                "metadata": 0,
                "read_error": 0,
                "processed": 0,
            }
            errors = []
            for policy in plan.refresh:
                if max_pages is not None and counts["processed"] >= max_pages:
                    break
                outcome, detail = _store_page(
                    self, policy, grouped.get(policy.id, []), read_policy, today
                )
                counts["processed"] += 1
                if outcome in counts:
                    counts[outcome] += 1
                if detail:
                    errors.append(detail)
                    logger.warning("Index page %s: %s", policy.id, detail.split(":", 1)[-1].strip())
            self.degraded = False
            report = format_sync_report(len(inventory), plan, counts, errors)
            logger.warning(report)
            return report
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


def _store_page(
    index: PolicyIndex, policy, stored: list[StoredChunk], read_policy, today
) -> tuple[str, str]:
    """Return (outcome, detail). Embedding failure still stores the text for full-text search."""
    try:
        document = read_policy(index._notion, policy)
    except Exception as exc:  # noqa: BLE001 -- keep indexing the other pages
        return "read_error", f"{policy.id}: read failed ({type(exc).__name__})"
    chunks = chunk_policy(policy.title or document.title, document.text)
    if not chunks:
        index._store.delete_pages([policy.id])
        return "metadata", f"{policy.id}: page had no text"
    model, dims = index._embedder.model, index._embedder.dimensions
    is_live = policy.is_live(today)
    if same_content(chunks, stored, model, dims):
        index._store.touch_page(
            policy.id, policy.title or "", policy.last_edited_time or "", is_live
        )
        return "metadata", ""
    vectors = index._embedder.embed([chunk.embed_text for chunk in chunks])
    if not isinstance(vectors, list) or len(vectors) != len(chunks):
        vectors = [None] * len(chunks)
    failed = sum(vector is None for vector in vectors)
    if failed:
        reason = getattr(index._embedder, "last_error", "") or "no vectors returned"
        detail = (
            f"{policy.id}: embedding failed ({reason}); "
            f"{failed} of {len(chunks)} chunks stored as text only"
        )
        outcome = "without_embeddings"
    else:
        detail, outcome = "", "embedded"
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
                "embedding": (
                    ""
                    if vector is None
                    else "[" + ",".join(f"{value:.6f}" for value in vector) + "]"
                ),
                "embedding_model": None if vector is None else model,
                "embedding_dimensions": None if vector is None else dims,
            }
        )
    index._store.replace_page(policy.id, payload)
    return outcome, detail


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
                has_embedding=row.get("has_embedding", True) is not False,
            )
        )
    return grouped


def _unique(items: list[str]) -> list[str]:
    seen = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _titles(rows: list[dict]) -> str:
    names = _unique([brief(row.get("title") or "Untitled", 60) for row in rows])
    return ", ".join(names[:5]) or "none"


def _title_by_id(rows: list[dict]) -> dict[str, str]:
    names = {}
    for row in rows:
        page_id = row.get("page_id")
        title = row.get("title")
        if page_id and title and page_id not in names:
            names[page_id] = brief(title, 60)
    return names


def _top_similarity(rows: list[dict]):
    values = []
    for row in rows:
        raw = row.get("similarity")
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


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
            emit(
                "Expanding neighbour chunks",
                "Neighbour lookup failed. Using the matched chunks only.",
            )
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
