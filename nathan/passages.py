"""Section lists and focused excerpts for long policy documents."""

import re

from nathan.policy_search import tokens

HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
FULL_TEXT_LIMIT = 16_000
EXCERPT_LIMIT = 16_000
_NOT_STATED = {"not stated", "not stated in the supplied text"}
_OMISSION_NOTE = (
    "\n\n[Other sections of this policy were retrieved but omitted here because they "
    "did not match the question. Omitted sections are not evidence of absence.]\n"
)


def section_titles(text):
    return [match.group(1).strip() for match in HEADING.finditer(text or "")]


def split_sections(text):
    """Return (heading, body) pairs. Text before the first heading has an empty heading."""
    body = text or ""
    matches = list(HEADING.finditer(body))
    if not matches:
        return [("", body)]
    parts = []
    if matches[0].start() > 0:
        parts.append(("", body[: matches[0].start()]))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        parts.append((match.group(1).strip(), body[match.start() : end]))
    return parts


def section_in_text(section, text):
    """True when a citation quotes the retrieved text, or explicitly says it is not stated."""
    needle = " ".join((section or "").casefold().split()).strip(" .:-")
    if needle in _NOT_STATED:
        return True
    if not needle:
        return False
    return needle in " ".join((text or "").casefold().split())


def pack_text(question, text):
    """Return text, scope, headings and omitted headings.

    Short documents are returned whole. A long document is narrowed to the opening
    section and the sections that match the question. If nothing matches, the full
    text is kept so a weak match cannot hide the policy.
    """
    body = text or ""
    titles = section_titles(body)
    if len(body) <= FULL_TEXT_LIMIT:
        return body, "full", titles, []
    parts = split_sections(body)
    if len(parts) <= 1:
        return body, "full", titles, []
    query = tokens(question)
    scores = []
    for heading, part in parts:
        scores.append(3 * len(tokens(heading) & query) + len(tokens(part) & query))
    if not any(scores):
        return body, "full", titles, []
    included, size = [], 0
    for index, (_, part) in enumerate(parts):
        if scores[index] <= 0 and index != 0:
            continue
        if included and size + len(part) > EXCERPT_LIMIT:
            continue
        included.append(index)
        size += len(part)
    omitted = [
        heading for index, (heading, _) in enumerate(parts) if index not in included and heading
    ]
    if not omitted:
        return body, "full", titles, []
    excerpt = "\n\n".join(parts[index][1].strip() for index in included)
    return excerpt + _OMISSION_NOTE, "relevant_sections", titles, omitted
