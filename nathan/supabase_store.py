"""Supabase REST client for the policy chunk table.

The service role key is sent only as a header. It is never written to a log line.
"""

import logging

import httpx

logger = logging.getLogger(__name__)


class IndexUnavailable(Exception):
    """The index could not be used. Callers fall back to keyword search."""


class SupabaseStore:
    def __init__(self, url: str, service_key: str, transport=None):
        self.url = url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.url + "/rest/v1/",
            timeout=30,
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            },
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
        rows, offset = [], 0
        while True:
            batch = self._get(
                "policy_chunks",
                params={
                    "select": (
                        "page_id,chunk_index,content_hash,last_edited_time,"
                        "embedding_model,embedding_dimensions"
                    ),
                    "order": "page_id.asc,chunk_index.asc",
                    "limit": 1000,
                    "offset": offset,
                },
            )
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
        self, embedding: list[float], match_count: int, live_only: bool
    ) -> list[dict]:
        literal = "[" + ",".join(f"{value:.6f}" for value in embedding) + "]"
        return self._rpc(
            "search_policy_chunks_vector",
            {"query_embedding": literal, "match_count": match_count, "live_only": live_only},
        )

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
            logger.warning("Policy index HTTP %s on %s", response.status_code, path.split("?")[0])
            raise IndexUnavailable(f"Policy index returned HTTP {response.status_code}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            logger.warning("Policy index returned invalid JSON")
            raise IndexUnavailable("Policy index returned invalid JSON") from exc
