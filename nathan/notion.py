import time

import httpx

from nathan.config import DATA_SOURCE_ID
from nathan.domain import SourceError, UncertainWrite


class NotionClient:
    def __init__(self, token: str, transport=None):
        self.http = httpx.Client(
            base_url="https://api.notion.com/v1/",
            timeout=30,
            headers={"Authorization": f"Bearer {token}", "Notion-Version": "2025-09-03"},
            transport=transport,
        )

    def request(self, method: str, path: str, *, write=False, **kwargs) -> dict:
        attempts = 1 if write else 2
        for attempt in range(attempts):
            try:
                response = self.http.request(method, path, **kwargs)
                if response.status_code == 429 and not write and attempt == 0:
                    time.sleep(min(float(response.headers.get("Retry-After", "1")), 5))
                    continue
                if response.is_error:
                    if write and response.status_code >= 500:
                        raise UncertainWrite(
                            "Notion creation outcome is uncertain. Check the "
                            "collection before trying again."
                        )
                    if not write and response.status_code >= 500 and attempt == 0:
                        continue
                    raise SourceError(
                        "Notion could not complete the request. Check integration "
                        "access and the configured database properties."
                    )
                data = response.json()
                if not isinstance(data, dict):
                    raise TypeError("Invalid response")
                return data
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                if write:
                    raise UncertainWrite(
                        "Notion creation outcome is uncertain. Check the "
                        "collection before trying again."
                    ) from exc
                if attempt + 1 == attempts:
                    raise SourceError(
                        "Notion is unavailable or returned incomplete information. "
                        "Current policy information could not be verified."
                    ) from exc
        raise SourceError("Notion rate limit reached. Please try again shortly.")

    def paginate(self, method, path, payload=None):
        results, cursor, seen = [], None, set()
        while True:
            args = dict(payload or {}, page_size=100)
            if cursor:
                args["start_cursor"] = cursor
            data = self.request(
                method, path, **({"params": args} if method == "GET" else {"json": args})
            )
            if not isinstance(data.get("results"), list):
                raise SourceError("Notion returned an incomplete collection.")
            results.extend(data["results"])
            if not data.get("has_more"):
                return results
            cursor = data.get("next_cursor")
            if not cursor or cursor in seen:
                raise SourceError("Notion pagination could not be completed.")
            seen.add(cursor)

    def query(self, filter=None):
        return self.paginate(
            "POST", f"data_sources/{DATA_SOURCE_ID}/query", {"filter": filter} if filter else {}
        )

    def children(self, block_id):
        return self.paginate("GET", f"blocks/{block_id}/children")

    def page(self, page_id):
        return self.request("GET", f"pages/{page_id}")

    def markdown(self, page_id):
        return self.request(
            "GET",
            f"pages/{page_id}/markdown",
            params={"markdown_version": "v2"},
            headers={"Notion-Version": "2026-03-11"},
        )

    def close(self):
        self.http.close()
