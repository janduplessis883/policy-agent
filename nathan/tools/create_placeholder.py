import re
from datetime import date

from nathan.config import DATA_SOURCE_ID
from nathan.domain import SourceError, UncertainWrite, rich_text


def normalise(title):
    return re.sub(r"\W+", " ", title.casefold()).strip()


def create_placeholder(client, title: str, today: date, inventory: list, advice: str):
    """Create an authorised proposal from its saved inventory; caller holds the write lock."""
    title = title.strip()
    if not title or len(title) > 200:
        raise SourceError("A concise policy title is required.")
    if any(normalise(p.title) == normalise(title) for p in inventory):
        raise SourceError("An existing record already has this title. No placeholder was created.")
    advice = (advice or "").strip()
    if not advice or len(advice) > 1800:
        raise SourceError("A policy synopsis of 1–1800 characters is required in Agent Advice.")
    properties = {
        "Agent Advice": {"rich_text": [{"text": {"content": advice}}]},
        "Policy": {"title": [{"text": {"content": title}}]},
        "Tags": {"multi_select": [{"name": "NEW POLICY REQUIRED"}]},
        "Review_Date": {"date": {"start": today.isoformat()}},
    }
    page = client.request(
        "POST",
        "pages",
        write=True,
        json={
            "parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE_ID},
            "properties": properties,
            "children": [],
        },
    )
    try:
        page_id = page["id"]
        verified = client.page(page_id)
        props = verified["properties"]
        good = (
            rich_text(props["Policy"]["title"]) == title
            and [t["name"] for t in props["Tags"]["multi_select"]] == ["NEW POLICY REQUIRED"]
            and props["Review_Date"]["date"]["start"] == today.isoformat()
            and rich_text(props["Agent Advice"]["rich_text"]) == advice
            and not client.children(page_id)
        )
        if not good:
            raise ValueError("Unexpected properties or page body")
        return {"id": page_id, "public_url": page.get("public_url")}
    except (SourceError, KeyError, ValueError) as exc:
        raise UncertainWrite(
            "A Notion record may have been created, but its required fields "
            "and empty body could not be verified. Check Notion before retrying."
        ) from exc
