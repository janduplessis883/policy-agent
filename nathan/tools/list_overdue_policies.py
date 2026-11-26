"""List out-of-date policies from inventory metadata. Never reads policy text.

The application calls this directly. Provider tool-calling is not used, so a question
about overdue or review dates must not depend on the model choosing this tool.
"""

import logging
import re
from datetime import date, timedelta

from nathan.domain import review_day
from nathan.register import policy_status

logger = logging.getLogger(__name__)

DUE_SOON_DAYS = 30
DESCRIPTION = (
    "List overdue, expired, out-of-date and due-soon policies from inventory metadata "
    "only: title, owner, review date, status and tags. Does not read policy text and "
    "is not limited to 15 policies."
)
_PROCEDURE = re.compile(r"\b(procedure|how do i|how should i|what should i do|steps to)\b")
_OVERDUE = re.compile(
    r"("
    r"\boverdue\b"
    r"|\bout[\s-]+of[\s-]+date\b"
    r"|\bexpired\b"
    r"|\bpast\s+(?:its\s+|their\s+|the\s+)?review\b"
    r"|\bdue\s+for\s+review\b"
    r"|\breview\s+dates?\b"
    r"|\bneeds?\s+review(?:ing)?\b"
    r"|\bdue\s+soon\b"
    r"|\bnext\s+30\s+days\b"
    r"|\bwithin\s+30\s+days\b"
    r"|\bcoming\s+up\s+for\s+review\b"
    r"|\bup\s+for\s+review\b"
    r")"
)
_DUE_SOON_FOCUS = re.compile(r"\b(due soon|next 30 days|within 30 days|coming up for review)\b")
_HELD = {"Draft", "Placeholder", "Archived", "Superseded", "Template", "Not in use"}
_UNDATED = {"No review date", "Review date unreadable"}


def is_overdue_question(question):
    """True for an inventory list of policies that are out of date or due soon."""
    text = " ".join((question or "").casefold().split())
    if not text or _PROCEDURE.search(text):
        return False
    return bool(_OVERDUE.search(text))


def list_overdue_policies(inventory, today, question="", link=None):
    """Every matching record. The caller has already paginated the whole inventory."""
    link = link or (lambda title, url: title)
    soon_until = today + timedelta(days=DUE_SOON_DAYS)
    overdue, due_soon, undated, held = [], [], [], []
    for policy in inventory or []:
        try:
            status = policy_status(policy, today)
            day = review_day(getattr(policy, "review_date", None))
        except Exception:
            logger.exception("Could not classify policy %s", getattr(policy, "id", None))
            undated.append(policy)
            continue
        if status == "Overdue":
            overdue.append(policy)
        elif status in _HELD:
            held.append(policy)
        elif status in _UNDATED or not isinstance(day, date):
            undated.append(policy)
        elif today < day <= soon_until:
            due_soon.append(policy)
    overdue.sort(key=_sort_key)
    due_soon.sort(key=_sort_key)
    undated.sort(key=_sort_key)
    focus_soon = bool(_DUE_SOON_FOCUS.search(" ".join((question or "").casefold().split())))
    lines = [
        f"**Overdue policies** on {today.isoformat()} (Europe/London).",
        "",
        (
            "Taken from titles, owners, tags and Review_Date only. "
            "Policy text was not read. This list is not limited to 15 policies."
        ),
        "",
        (
            f"**{len(overdue)}** overdue. **{len(due_soon)}** due within {DUE_SOON_DAYS} days. "
            f"**{len(undated)}** with no usable review date. **{len(held)}** draft, "
            "placeholder or archived."
        ),
    ]
    sections = [
        ("Overdue", overdue),
        (f"Due within {DUE_SOON_DAYS} days", due_soon),
        ("No usable review date", undated),
    ]
    if focus_soon:
        sections = [sections[1], sections[0], sections[2]]
    for heading, group in sections:
        lines.extend(["", f"### {heading} ({len(group)})"])
        if not group:
            lines.append("None.")
            continue
        for policy in group:
            try:
                lines.append(_line(policy, today, link))
            except Exception:
                logger.exception("Could not render policy %s", getattr(policy, "id", None))
                lines.append("- Unreadable record · review date unreadable")
    if held:
        lines.extend(
            [
                "",
                (
                    f"{len(held)} draft, placeholder, archived, superseded or template records "
                    "are not Live and are not included in the overdue list."
                ),
            ]
        )
    return "\n".join(lines)


def _sort_key(policy):
    day = review_day(getattr(policy, "review_date", None))
    when = day.toordinal() if isinstance(day, date) else 10**9
    title = getattr(policy, "title", "")
    label = title.casefold() if isinstance(title, str) else ""
    return (when, label)


def _line(policy, today, link):
    title = getattr(policy, "title", "")
    title = title.strip() if isinstance(title, str) else ""
    title = title or "Untitled policy"
    url = getattr(policy, "public_url", None)
    status = policy_status(policy, today)
    parts = [link(title, url if isinstance(url, str) else None), status]
    day = review_day(getattr(policy, "review_date", None))
    if isinstance(day, date):
        parts.append(f"review date {day.isoformat()}")
    owners = [
        owner.strip()
        for owner in (getattr(policy, "owners", None) or [])
        if isinstance(owner, str) and owner.strip()
    ]
    if owners:
        parts.append(", ".join(owners))
    tags = [
        tag.strip()
        for tag in (getattr(policy, "tags", None) or [])
        if isinstance(tag, str) and tag.strip()
    ]
    if tags:
        parts.append("tags: " + ", ".join(tags))
    return "- " + " · ".join(parts)
