from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo


class SourceError(Exception):
    """A safe, user-facing source or validation failure."""


class UncertainWrite(SourceError):
    """A write may have succeeded; do not automatically retry."""


@dataclass
class Policy:
    id: str
    title: str
    review_date: str | None = None
    tags: list[str] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    public_url: str | None = None
    version: float | None = None
    excluded: bool = False
    last_edited_time: str | None = None

    def is_live(self, today: date) -> bool:
        try:
            if self.excluded or EXCLUDED_TAGS.intersection(normalised_tags(self.tags)):
                return False
            review = review_day(self.review_date)
            return isinstance(review, date) and review > today
        except Exception:  # noqa: BLE001 -- one bad record must not break currency checks
            return False


@dataclass
class Document:
    id: str
    title: str
    text: str
    complete: bool = True
    public_url: str | None = None
    indexed: bool = False


@dataclass
class Result:
    text: str
    proposal: str | None = None
    model: str | None = None
    proposal_advice: str | None = None
    proposal_inventory: list[Policy] | None = None
    conversation_context: dict = field(default_factory=dict)
    source_note: str | None = None


EXCLUDED_TAGS = {
    "draft",
    "placeholder",
    "new policy required",
    "archived",
    "superseded",
    "template",
    "surgery profile",
}
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")


def normalised_tags(tags) -> set[str]:
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, (list, tuple, set)):
        return set()
    return {tag.casefold().strip() for tag in tags if isinstance(tag, str) and tag.strip()}


def review_day(value):
    """London calendar day, None when missing, False when the value cannot be read."""
    if isinstance(value, datetime):
        value = value.isoformat()
    elif isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalised = text.replace("Z", "+00:00")
    try:
        if "T" in normalised:
            parsed = datetime.fromisoformat(normalised)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Europe/London"))
            return parsed.astimezone(ZoneInfo("Europe/London")).date()
        return date.fromisoformat(normalised)
    except (ValueError, TypeError):
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return False


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def rich_text(items) -> str:
    parts = []
    for item in _as_list(items):
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        plain = item.get("plain_text")
        if isinstance(plain, str):
            parts.append(plain)
            continue
        text = item.get("text")
        if isinstance(text, dict) and isinstance(text.get("content"), str):
            parts.append(text["content"])
    return "".join(parts)


def _date_start(value, depth=0):
    if depth > 6 or value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            found = _date_start(item, depth + 1)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    start = value.get("start")
    if isinstance(start, str) and start.strip():
        return start.strip()
    for key in ("date", "formula", "rollup"):
        found = _date_start(value.get(key), depth + 1)
        if found:
            return found
    kind = value.get("type")
    if isinstance(kind, str):
        inner = value.get(kind)
        if kind == "string" and isinstance(inner, str) and inner.strip():
            return inner.strip()
        found = _date_start(inner, depth + 1)
        if found:
            return found
    return _date_start(value.get("array"), depth + 1)


def property_date(prop):
    """Read a date from a date, formula, rollup, rich text or timestamp property."""
    prop = _as_dict(prop)
    for key in ("created_time", "last_edited_time"):
        value = prop.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("date", "formula", "rollup"):
        found = _date_start(prop.get(key))
        if found:
            return found
    text = rich_text(prop.get("rich_text")).strip()
    return text or None


def property_names(prop) -> list[str]:
    """Names from multi-select, select, status, people or rich text. Null names are skipped."""
    prop = _as_dict(prop)
    names = []
    for item in _as_list(prop.get("multi_select")):
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip():
            names.append(item["name"].strip())
    for key in ("select", "status"):
        item = prop.get(key)
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip():
            names.append(item["name"].strip())
    for person in _as_list(prop.get("people")):
        if (
            isinstance(person, dict)
            and isinstance(person.get("name"), str)
            and person["name"].strip()
        ):
            names.append(person["name"].strip())
    text = rich_text(prop.get("rich_text")).strip()
    if text:
        names.append(text)
    return names


def _properties(page) -> dict:
    if not isinstance(page, dict):
        return {}
    props = page.get("properties")
    return props if isinstance(props, dict) else {}


def _title_items(props: dict):
    policy = props.get("Policy")
    if isinstance(policy, dict):
        return policy.get("title")
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return prop.get("title")
    return []


def policy_from_page(page: dict) -> Policy:
    """Best-effort record. Odd or missing Notion properties do not raise."""
    if not isinstance(page, dict):
        raise TypeError("Notion page was not an object")
    props = _properties(page)
    tags = property_names(props.get("Tags"))
    owners = property_names(props.get("Policy_Owner"))
    excluded = bool(page.get("archived") or page.get("in_trash"))
    for prop in props.values():
        if not isinstance(prop, dict) or prop.get("type") not in {"status", "select"}:
            continue
        state = prop.get(prop["type"])
        name = state.get("name") if isinstance(state, dict) else None
        if isinstance(name, str) and name.casefold().strip() in {"draft", "archived", "superseded"}:
            excluded = True
    version_prop = props.get("Version")
    version = version_prop.get("number") if isinstance(version_prop, dict) else None
    if isinstance(version, bool) or not isinstance(version, (int, float)):
        version = None
    page_id = page.get("id")
    if not isinstance(page_id, str) or not page_id.strip():
        raise TypeError("Notion page had no id")
    public_url = page.get("public_url")
    edited = page.get("last_edited_time")
    return Policy(
        id=page_id,
        title=rich_text(_title_items(props)),
        review_date=property_date(props.get("Review_Date")),
        tags=tags,
        owners=owners,
        public_url=public_url if isinstance(public_url, str) else None,
        version=version,
        excluded=excluded,
        last_edited_time=edited if isinstance(edited, str) else None,
    )
