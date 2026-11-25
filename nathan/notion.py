import logging
import re
import time

import httpx

from nathan.config import DATA_SOURCE_ID
from nathan.domain import SourceError, UncertainWrite

logger = logging.getLogger(__name__)


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
                    if write:
                        guidance = {
                            400: "Check the Policies property names and types: Policy (title), "
                            "Tags (multi-select), Review_Date (date), and Agent Advice (rich text).",
                            401: "Check the Notion integration token.",
                            403: "Enable insert content permission for the Notion integration "
                            "and share the Policies database with it.",
                            404: "Check the configured data source ID and share the Policies database "
                            "with the Notion integration.",
                            429: "Notion rate limit reached. Wait briefly before trying again.",
                        }.get(
                            response.status_code,
                            "Check Notion integration access and database settings.",
                        )
                        raise SourceError(
                            f"Notion rejected placeholder creation (HTTP {response.status_code}). "
                            f"No record was created. {guidance}"
                        )
                    raise _read_error(response)
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
                    logger.warning("Notion read failed: %s", _scrub(str(exc)))
                    raise SourceError(
                        "Notion could not be reached. Check the connection and the "
                        "integration token, then try again. The details are in the server log."
                    ) from exc
        raise SourceError("Notion rate limit reached. Please try again shortly.")

    def paginate(self, method, path, payload=None, *, params=None):
        results, cursor, seen = [], None, set()
        while True:
            args = dict(payload or {}, page_size=100)
            if cursor:
                args["start_cursor"] = cursor
            data = self.request(
                method,
                path,
                **({"params": args} if method == "GET" else {"json": args, "params": params}),
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
            "POST",
            f"data_sources/{DATA_SOURCE_ID}/query",
            {"filter": filter} if filter else {},
            params={"filter_properties": ["Policy", "Tags", "Review_Date", "Policy_Owner"]},
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


def _scrub(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:secret_|ntn_)[A-Za-z0-9]+", "[redacted]", text)
    return text


def _read_error(response) -> SourceError:
    status = getattr(response, "status_code", None)
    body = ""
    try:
        body = response.text
    except Exception:  # noqa: BLE001 -- logging must not raise
        body = ""
    logger.warning("Notion read failed HTTP %s: %s", status, _scrub(body)[:500])
    guidance = {
        400: "Notion rejected the policy query (HTTP 400). Check the properties "
        "Policy (title), Tags, Review_Date and Policy_Owner.",
        401: "Notion rejected the integration token (HTTP 401). Check NOTION_TOKEN.",
        403: "Notion refused access (HTTP 403). Share the Policies database with the "
        "integration and allow it to read content.",
        404: "Notion could not find the policy data source (HTTP 404). Check the data source "
        "id and that the integration can see the database.",
        429: "Notion rate limit reached (HTTP 429). Wait briefly and try again.",
    }.get(
        status,
        f"Notion could not complete the read (HTTP {status}). "
        "Check integration access and the database properties.",
    )
    return SourceError(guidance)
