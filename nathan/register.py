"""Answer register questions from policy metadata, not from model prose."""

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

LIST_LIMIT = 40
_PROCEDURE = re.compile(r"\b(procedure|how do i|how should i|what should i do|steps to)\b")
_CONTENT = re.compile(r"\b(about|on|for|regarding|covering|cover|describe)\b")
_STATUS_QUESTION = re.compile(r"\b(overdue|due for review|review dates?|policy register)\b")
_HELD = re.compile(r"\b(drafts?|placeholders?|archived|superseded)\b")
_COLLECTION = re.compile(r"\b(which|what|list|how many)\b")
_CATALOGUE = re.compile(r"\b(list|register|how many|every policy|all policies)\b")
_TAG_STATUS = (
    ("Placeholder", {"placeholder", "new policy required"}),
    ("Draft", {"draft"}),
    ("Archived", {"archived"}),
    ("Superseded", {"superseded"}),
    ("Template", {"template"}),
)
_GROUPS = (
    "Overdue",
    "No review date",
    "Review date unreadable",
    "Draft",
    "Placeholder",
    "Archived",
    "Superseded",
    "Template",
    "Not in use",
    "Live",
)


def is_register_question(question):
    """True when the staff member is asking about the inventory, not a procedure."""
    text = " ".join((question or "").casefold().split())
    if not text or _PROCEDURE.search(text):
        return False
    if _STATUS_QUESTION.search(text):
        return True
    if _HELD.search(text) and re.search(r"\bpolic", text) and not _CONTENT.search(text):
        return True
    return bool(
        _COLLECTION.search(text) and re.search(r"\bpolicies\b", text) and not _CONTENT.search(text)
    )


def review_day(policy):
    """London calendar day, None when missing, False when the stored value cannot be read."""
    value = policy.review_date or ""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        if "T" in value:
            return datetime.fromisoformat(value).astimezone(ZoneInfo("Europe/London")).date()
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return False


def policy_status(policy, today):
    """Same Live rule as Policy.is_live, with a label staff can read."""
    tags = {tag.casefold().strip() for tag in policy.tags}
    for label, names in _TAG_STATUS:
        if tags & names:
            return label
    if policy.excluded:
        return "Not in use"
    day = review_day(policy)
    if day is None:
        return "No review date"
    if day is False:
        return "Review date unreadable"
    if day > today:
        return "Live"
    return "Overdue"


def render_register(question, inventory, today, link):
    """Exact counts and titles. Dates are not paraphrased by the model."""
    rows = [(policy, policy_status(policy, today)) for policy in inventory]
    counts = {status: 0 for status in _GROUPS}
    for _, status in rows:
        counts[status] = counts.get(status, 0) + 1
    total = len(rows)
    live = counts["Live"]
    attention = total - live
    lines = [
        f"**Policy register** on {today.isoformat()} (Europe/London).",
        "",
        (
            "Taken from policy titles, tags, owners and Review_Date. "
            "Policy text was not read, and this is not a procedure."
        ),
        "",
        f"**{total}** policies. **{live}** Live. **{attention}** not Live.",
    ]
    if total == 0:
        lines.append("The inventory has no policy records. The Surgery Profile is not included.")
        return "\n".join(lines)
    show_live = bool(_CATALOGUE.search(question or ""))
    for status in _GROUPS:
        if status == "Live" and not show_live:
            continue
        group = [policy for policy, item_status in rows if item_status == status]
        if not group:
            continue
        lines.extend(["", f"### {status} ({len(group)})"])
        ordered = sorted(group, key=lambda policy: _sort_key(policy))
        shown = ordered[:LIST_LIMIT]
        for policy in shown:
            lines.append(_line(policy, status, link))
        hidden = len(ordered) - len(shown)
        if hidden:
            lines.append(f"- And {hidden} more.")
    if not show_live and live:
        lines.extend(
            [
                "",
                (
                    f"{live} Live policies are current and are not listed here. "
                    "Ask for the policy register to see every title."
                ),
            ]
        )
    return "\n".join(lines)


def _sort_key(policy):
    day = review_day(policy)
    when = day.toordinal() if isinstance(day, date) else 10**9
    return (when, (policy.title or "").casefold())


def _line(policy, status, link):
    title = (policy.title or "").strip() or "Untitled policy"
    parts = [link(title, policy.public_url), status]
    day = review_day(policy)
    if isinstance(day, date) and status in {"Overdue", "Live"}:
        parts.append(f"review date {day.isoformat()}")
    if policy.owners:
        parts.append(", ".join(policy.owners))
    return "- " + " · ".join(parts)
