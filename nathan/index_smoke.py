"""Nightly check against a staging Notion database and Supabase project.

Exits 0 without doing any work when NOTION_TOKEN, SUPABASE_URL or
SUPABASE_SERVICE_ROLE_KEY is missing. Prints counts only.
"""

import os
import sys

from nathan.notion import NotionClient
from nathan.supabase_store import SupabaseStore
from nathan.tools.list_policies import list_policies


def main() -> int:
    token = os.environ.get("NOTION_TOKEN", "").strip()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not token or not url or not key:
        print(
            "SKIP: NOTION_TOKEN, SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is absent. "
            "No staging call was made."
        )
        return 0
    notion = NotionClient(token)
    store = SupabaseStore(url, key)
    try:
        policies = list_policies(notion)
        rows = store.fetch_state()
    finally:
        notion.close()
        store.close()
    pages = {row.get("page_id") for row in rows if row.get("page_id")}
    print(f"Notion policies: {len(policies)}. Indexed pages: {len(pages)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
