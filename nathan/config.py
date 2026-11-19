from collections.abc import Mapping
from dataclasses import dataclass

from nathan.domain import SourceError

DATA_SOURCE_ID = "261fdfd6-8a97-81b1-9e23-000bf4245172"
DATABASE_ID = "261fdfd6-8a97-8060-af8a-f3f29754e96e"
QUESTION_LOG_DATA_SOURCE_ID = "41dfdfd6-8a97-80f2-addc-000bae8d9225"
BASE_URL = "https://freellmapi-4ace.srv1395197.hstgr.cloud/v1"


@dataclass(frozen=True)
class Settings:
    api_key: str
    notion_token: str
    access_pin: str
    base_url: str = BASE_URL
    model: str = "auto"

    @classmethod
    def from_secrets(cls, secrets: Mapping):
        keys = ["FREELLMAPI_SPI_KEY", "NOTION_TOKEN", "ACCESS_PIN"]
        if any(not isinstance(secrets.get(k), str) or not secrets[k].strip() for k in keys):
            raise SourceError(
                "Configure FREELLMAPI_SPI_KEY, NOTION_TOKEN and ACCESS_PIN as "
                "quoted strings in .streamlit/secrets.toml."
            )
        return cls(*(secrets[k].strip() for k in keys))
