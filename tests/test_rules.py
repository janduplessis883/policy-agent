from datetime import UTC, date, datetime

import pytest

from nathan.auth import PinGuard
from nathan.config import BASE_URL, Settings
from nathan.domain import Policy, SourceError
from nathan.tools.current_date import current_date


def test_london_date_uses_british_summer_time():
    assert current_date(datetime(2026, 6, 1, 23, 30, tzinfo=UTC)) == date(2026, 6, 2)


@pytest.mark.parametrize(
    "review, tags, expected",
    [
        ("2026-11-17", [], True),
        ("2026-11-16", [], False),
        ("2026-11-15", [], False),
        (None, [], False),
        ("2027-01-01", ["NEW POLICY REQUIRED"], False),
        ("2027-01-01", ["Draft"], False),
        ("garbage", [], False),
    ],
)
def test_currency(review, tags, expected):
    p = Policy(id="p", title="Test", review_date=review, tags=tags)
    assert p.is_live(date(2026, 11, 16)) is expected


def test_currency_ignores_null_tags_and_odd_dates():
    today = date(2026, 11, 16)
    assert Policy(id="p", title="Test", review_date="2027-01-01", tags=[None, "Clinical"]).is_live(
        today
    )
    assert not Policy(id="p", title="Test", review_date="15/01/2020", tags=None).is_live(today)
    assert Policy(id="p", title="Test", review_date="01/01/2027").is_live(today)


def _required_secrets():
    return {"FREELLMAPI_SPI_KEY": "key", "NOTION_TOKEN": "token", "ACCESS_PIN": "pin"}


def test_optional_endpoint_settings_keep_defaults(monkeypatch):
    monkeypatch.delenv("FREELLMAPI_BASE_URL", raising=False)
    monkeypatch.delenv("FREELLMAPI_MODEL", raising=False)
    settings = Settings.from_secrets({**_required_secrets(), "FREELLMAPI_MODEL": "  "})
    assert settings.base_url == BASE_URL
    assert settings.model == "auto"
    monkeypatch.setenv("FREELLMAPI_MODEL", "from-env")
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "https://example.test/v1")
    # A blank secret does not fall through to the environment.
    assert Settings.from_secrets({**_required_secrets(), "FREELLMAPI_MODEL": ""}).model == "auto"
    overridden = Settings.from_secrets(_required_secrets())
    assert overridden.model == "from-env"
    assert overridden.base_url == "https://example.test/v1"
    explicit = Settings.from_secrets({**_required_secrets(), "FREELLMAPI_MODEL": "named-model"})
    assert explicit.model == "named-model"


def test_supabase_is_optional_and_https_only(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_EMBEDDING_MODEL", raising=False)
    plain = Settings.from_secrets(_required_secrets())
    assert plain.index_enabled is False
    enabled = Settings.from_secrets(
        {
            **_required_secrets(),
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role",
            "ADMIN_PIN": "admin-pin",
        }
    )
    assert enabled.index_enabled is True
    assert enabled.admin_pin == "admin-pin"
    assert enabled.embedding_model == "openai/text-embedding-3-small"
    assert enabled.openrouter_api_key == ""
    assert enabled.model == "auto"
    chosen = Settings.from_secrets(
        {
            **_required_secrets(),
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role",
            "OPENROUTER_API_KEY": "sk-or-test",
            "OPENROUTER_EMBEDDING_MODEL": "openai/text-embedding-3-small",
            "FREELLMAPI_EMBEDDING_MODEL": "auto",
        }
    )
    assert chosen.openrouter_api_key == "sk-or-test"
    assert chosen.embedding_model == "openai/text-embedding-3-small"
    assert chosen.model == "auto"
    assert "freellmapi" in chosen.base_url
    with pytest.raises(SourceError, match="SUPABASE_URL"):
        Settings.from_secrets({**_required_secrets(), "SUPABASE_URL": "http://example.supabase.co"})


def test_insecure_model_url_is_rejected():
    with pytest.raises(SourceError, match="https"):
        Settings.from_secrets(
            {**_required_secrets(), "FREELLMAPI_BASE_URL": "http://example.test/v1"}
        )


def test_pin_throttles_and_does_not_accept_blank():
    guard = PinGuard(max_attempts=2, cooldown=60)
    assert not guard.verify("bad", "1234", now=0)
    assert not guard.verify("bad", "1234", now=1)
    assert not guard.verify("1234", "1234", now=2)
    assert guard.verify("1234", "1234", now=62)
    assert not guard.verify("", "", now=63)
