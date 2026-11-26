"""Rank lightweight title/tag matches without fetching policy bodies."""

import re

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "bot",
    "by",
    "can",
    "check",
    "coverage",
    "create",
    "do",
    "does",
    "for",
    "from",
    "give",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "missing",
    "need",
    "new",
    "of",
    "on",
    "or",
    "our",
    "please",
    "policies",
    "policy",
    "practice",
    "procedure",
    "related",
    "review",
    "surgery",
    "that",
    "the",
    "their",
    "this",
    "to",
    "us",
    "we",
    "what",
    "when",
    "whether",
    "which",
    "with",
    "written",
}
# Direct equivalents are stronger evidence than broader subject matches.
TOPICS = (
    (
        ("ecg", "ecgs", "electrocardiogram", "electrocardiograms", "electrocardiography"),
        ("cardiac", "cardiology", "diagnostic"),
    ),
    (
        ("cold chain", "vaccine storage", "vaccine fridge", "vaccination fridge"),
        ("vaccine", "immunisation", "immunization"),
    ),
    (("immunisation", "immunization", "vaccination", "vaccine"), ("cold chain",)),
    (
        ("blood results", "test results", "abnormal results", "results handling"),
        ("pathology", "laboratory", "results"),
    ),
    (("safeguarding", "safeguarding children", "child protection", "adult protection"), ()),
    (("end of life", "palliative", "eol"), ()),
    (("infection control", "infection prevention", "ipc"), ("hygiene", "ppe")),
    (("repeat prescription", "repeat prescribing"), ("prescribing", "medicines")),
    (("resuscitation", "cpr", "basic life support", "bls"), ("emergency", "aed")),
    (("cervical screening", "smear", "smear test"), ("screening",)),
    (("complaint", "complaints", "patient concern"), ()),
    (
        (
            "information governance",
            "confidentiality",
            "confidential",
            "data protection",
            "gdpr",
            "caldicott",
        ),
        ("privacy", "record"),
    ),
    (
        ("gillick", "gillick competence", "fraser competence", "young people"),
        ("confidentiality", "consent"),
    ),
    (
        ("medical record", "medical records", "subject access", "subject access request"),
        ("record",),
    ),
    (
        ("proxy access", "online proxy"),
        ("proxy", "online"),
    ),
    (
        ("information governance incident", "data breach"),
        ("information", "governance"),
    ),
    (("duty doctor", "duty gp", "urgent callback", "urgent callbacks"), ("callback", "duty")),
    (("chaperone", "intimate examination"), ("examination",)),
    (("significant event", "learning event"), ("incident",)),
    (("duty of candour", "duty of candor", "candour"), ()),
    (("controlled drug", "controlled drugs"), ("medicines", "prescribing")),
    (("needlestick", "needle stick", "sharps"), ("infection",)),
    (("did not attend", "missed appointment"), ("appointment",)),
    (("mental capacity", "capacity assessment"), ("consent",)),
    (("whistleblowing", "freedom to speak up", "speaking up"), ()),
)

_FOLLOW_UP_ABOUT = re.compile(r"^(?:what|how) about\b", re.IGNORECASE)
_FOLLOW_UP_REFERENCE = re.compile(
    r"\b(that|this|those|these|same|above|previous|earlier)\b",
    re.IGNORECASE,
)
_FOLLOW_UP_PRONOUN = re.compile(r"\b(it|them|they)\b", re.IGNORECASE)


def _stem(word):
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    # Keep a trailing double-s ("address", "business") intact.
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text):
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return {_stem(word) for word in words if word not in STOP_WORDS}


def is_followup(question):
    """Short references such as "that" or "what about" need the previous topic."""
    text = " ".join((question or "").split())
    if _FOLLOW_UP_ABOUT.match(text):
        return True
    words = text.split()
    if len(words) > 14:
        return False
    if _FOLLOW_UP_REFERENCE.search(text):
        return True
    return len(words) <= 6 and bool(_FOLLOW_UP_PRONOUN.search(text))


def _topic_terms(text):
    equivalents, broader, phrases = set(), set(), []
    for aliases, related in TOPICS:
        hit = [alias for alias in aliases if re.search(rf"\b{re.escape(alias)}s?\b", text)]
        if not hit:
            continue
        phrases.extend(hit)
        equivalents.update(tokens(" ".join(aliases)))
        broader.update(tokens(" ".join(related)))
    return equivalents, broader, phrases


def _weak_single_token(overlap, phrases):
    """A lone word from a multi-word topic ("chain" in "cold chain") is not enough."""
    if len(overlap) != 1:
        return False
    token = next(iter(overlap))
    single, phrase_only = set(), set()
    for phrase in phrases:
        words = tokens(phrase)
        if len(phrase.split()) == 1:
            single.update(words)
        else:
            phrase_only.update(words)
    return token not in single and token in phrase_only


def ranked_policy_ids(question, inventory):
    """Return matching IDs strongest first; semantic selection can add other candidates."""
    query = tokens(question)
    text = " ".join(re.findall(r"[a-z0-9]+", question.casefold()))
    equivalents, broader, phrases = _topic_terms(text)
    ranked = []
    for index, policy in enumerate(inventory):
        title, tags = tokens(policy.title), tokens(" ".join(policy.tags))
        overlap = (title | tags) & (query | equivalents | broader)
        if not overlap or _weak_single_token(overlap, phrases):
            continue
        haystack = f"{policy.title} {' '.join(policy.tags)}".casefold()
        phrase_hit = any(len(phrase.split()) > 1 and phrase in haystack for phrase in phrases)
        score = (
            6 * len(title & query)
            + 5 * len(tags & query)
            + 4 * len(title & equivalents)
            + 3 * len(tags & equivalents)
            + len((title | tags) & broader)
            + (8 if phrase_hit else 0)
        )
        if score:
            ranked.append((-score, index, policy.id))
    return [policy_id for _, _, policy_id in sorted(ranked)]


def lexical_policy_ids(question, inventory, previous_question=""):
    """Rank the question, and use the previous topic when this turn only refers back to it."""
    matches = ranked_policy_ids(question, inventory)
    prior = previous_question.strip() if isinstance(previous_question, str) else ""
    if matches or not prior or not is_followup(question):
        return matches
    return ranked_policy_ids(prior, inventory)
