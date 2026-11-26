"""Run a full index update. Usage: python -m nathan.index_cli

Reads SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, NOTION_TOKEN, FREELLMAPI_SPI_KEY
and ACCESS_PIN from the environment. Prints counts only, never page text or keys.
"""

import os
import sys

from nathan.config import Settings
from nathan.notion import NotionClient
from nathan.policy_index import PolicyIndex


def main() -> int:
    settings = Settings.from_secrets(os.environ)
    if not settings.index_enabled:
        print("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is not set. Nothing was indexed.")
        return 0
    notion = NotionClient(settings.notion_token)
    try:
        print(PolicyIndex(settings, notion).sync_incremental(max_pages=None))
    finally:
        notion.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
