import os
from collections.abc import Mapping
from dataclasses import dataclass

from nathan.domain import SourceError

DATA_SOURCE_ID = "261fdfd6-8a97-81b1-9e23-000bf4245172"
DATABASE_ID = "261fdfd6-8a97-8060-af8a-f3f29754e96e"
BASE_URL = "https://freellmapi-4ace.srv1395197.hstgr.cloud/v1"


def _optional_setting(secrets: Mapping, key: str) -> str:
    """A present secret wins, including a blank. Otherwise use the environment."""
    if key in secrets:
        value = secrets.get(key, "")
        return value.strip() if isinstance(value, str) else ""
    value = os.environ.get(key, "")
    return value.strip() if isinstance(value, str) else ""


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
        base_url = _optional_setting(secrets, "FREELLMAPI_BASE_URL") or BASE_URL
        model = _optional_setting(secrets, "FREELLMAPI_MODEL") or "auto"
        if not base_url.startswith("https://") or any(char in base_url for char in " \n\r"):
            raise SourceError(
                "FREELLMAPI_BASE_URL must be an https URL with no spaces. "
                "Remove it to use the default endpoint."
            )
        return cls(
            secrets["FREELLMAPI_SPI_KEY"].strip(),
            secrets["NOTION_TOKEN"].strip(),
            secrets["ACCESS_PIN"].strip(),
            base_url,
            model,
        )
