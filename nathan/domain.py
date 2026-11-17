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
        excluded = {
            "draft",
            "placeholder",
            "new policy required",
            "archived",
            "superseded",
            "template",
            "surgery profile",
        }
        if self.excluded or excluded.intersection(t.casefold().strip() for t in self.tags):
            return False
        try:
            value = self.review_date or ""
            review = (
                datetime.fromisoformat(value).astimezone(ZoneInfo("Europe/London")).date()
                if "T" in value
                else date.fromisoformat(value)
            )
            return review > today
        except (ValueError, TypeError):
            return False


@dataclass
class Document:
    id: str
    title: str
    text: str
    complete: bool = True
    public_url: str | None = None


@dataclass
class Result:
    text: str
    proposal: str | None = None
    model: str | None = None
    proposal_advice: str | None = None
    proposal_inventory: list[Policy] | None = None


def rich_text(items: list[dict]) -> str:
    return "".join(i.get("plain_text", i.get("text", {}).get("content", "")) for i in items)


def policy_from_page(page: dict) -> Policy:
    props = page.get("properties", {})
    tags = [t["name"] for t in props.get("Tags", {}).get("multi_select", [])]
    excluded = bool(page.get("archived") or page.get("in_trash"))
    for prop in props.values():
        if prop.get("type") in {"status", "select"}:
            state = prop.get(prop["type"]) or {}
            excluded |= state.get("name", "").casefold() in {"draft", "archived", "superseded"}
    return Policy(
        id=page["id"],
        title=rich_text(props.get("Policy", {}).get("title", [])),
        review_date=(props.get("Review_Date", {}).get("date") or {}).get("start"),
        tags=tags,
        owners=[o["name"] for o in props.get("Policy_Owner", {}).get("multi_select", [])],
        public_url=page.get("public_url"),
        version=props.get("Version", {}).get("number"),
        excluded=excluded,
        last_edited_time=page.get("last_edited_time"),
    )
