from datetime import datetime
from zoneinfo import ZoneInfo


def current_date(now: datetime | None = None):
    """Establish a fresh Europe/London date for each request."""
    now = now or datetime.now(ZoneInfo("Europe/London"))
    if now.tzinfo is None:
        raise ValueError("An aware datetime is required")
    return now.astimezone(ZoneInfo("Europe/London")).date()
