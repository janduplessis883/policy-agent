from datetime import UTC, date, datetime

import pytest

from nathan.auth import PinGuard
from nathan.domain import Policy
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


def test_pin_throttles_and_does_not_accept_blank():
    guard = PinGuard(max_attempts=2, cooldown=60)
    assert not guard.verify("bad", "1234", now=0)
    assert not guard.verify("bad", "1234", now=1)
    assert not guard.verify("1234", "1234", now=2)
    assert guard.verify("1234", "1234", now=62)
    assert not guard.verify("", "", now=63)
