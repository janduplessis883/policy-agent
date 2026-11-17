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
    (("safeguarding", "child protection", "adult protection"), ()),
    (("end of life", "palliative", "eol"), ()),
    (("infection control", "infection prevention", "ipc"), ("hygiene", "ppe")),
    (("repeat prescription", "repeat prescribing"), ("prescribing", "medicines")),
    (("resuscitation", "cpr", "basic life support", "bls"), ("emergency", "aed")),
    (("cervical screening", "smear", "smear test"), ("screening",)),
    (("complaint", "complaints", "patient concern"), ()),
)


def tokens(text):
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return {
        word[:-1] if len(word) > 4 and word.endswith("s") else word
        for word in words
        if word not in STOP_WORDS
    }


def ranked_policy_ids(question, inventory):
    """Return matching IDs strongest first; semantic selection can add other candidates."""
    query = tokens(question)
    equivalents, broader = set(), set()
    text = " ".join(re.findall(r"[a-z0-9]+", question.casefold()))
    for aliases, related in TOPICS:
        if any(re.search(r"\b" + re.escape(alias) + r"s?\b", text) for alias in aliases):
            equivalents.update(tokens(" ".join(aliases)))
            broader.update(tokens(" ".join(related)))
    ranked = []
    for index, policy in enumerate(inventory):
        title, tags = tokens(policy.title), tokens(" ".join(policy.tags))
        score = (
            6 * len(title & query)
            + 5 * len(tags & query)
            + 4 * len(title & equivalents)
            + 3 * len(tags & equivalents)
            + len((title | tags) & broader)
        )
        if score:
            ranked.append((-score, index, policy.id))
    return [policy_id for _, _, policy_id in sorted(ranked)]
