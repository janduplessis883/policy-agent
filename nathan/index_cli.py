"""Run a full index update. Usage: python -m nathan.index_cli

Reads SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, NOTION_TOKEN, FREELLMAPI_SPI_KEY,
ACCESS_PIN and OPENROUTER_API_KEY from the environment. Prints counts only,
never page text or keys. Chat still uses FreeLLMAPI. Embeddings use OpenRouter.
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
