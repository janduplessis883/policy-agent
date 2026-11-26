from datetime import date

from nathan.agent import Agent
from nathan.domain import Document, Policy
from nathan.model import fit_response_payload
from nathan.policy_search import lexical_policy_ids
from nathan.register import is_register_question
from nathan.tools.list_overdue_policies import is_overdue_question

SCENARIO = """
It is 4:45 pm on a Friday at Stanhope Mews Surgery.
A mother telephones reception regarding her 14-year-old daughter, who has missed two appointments. She reports that her daughter is increasingly withdrawn, has expressed suicidal thoughts and has stopped taking prescribed medication.
The mother demands an urgent GP appointment and full access to her daughter's medical records. However, the daughter's SystmOne record contains a confidential consultation in which she disclosed possible abuse by a family member and specifically requested that this information not be shared with her parents.
The receptionist promises a GP callback before 5:30 pm. At 5:15 pm, the duty GP leaves unexpectedly because of a family emergency. No alternative clinician has been formally allocated to urgent callbacks.
At 5:40 pm, the mother calls again, distressed and angry. She threatens to make a formal complaint and publish the receptionist's name on social media.
The receptionist discovers that the earlier call was incorrectly coded as a routine administrative query. The safeguarding concern was never escalated, and the callback has not happened.
A further complication emerges: the mother's online proxy access may expose confidential information recorded during her daughter's consultation.
As Practice Manager, what precise actions must you take immediately, before closing the surgery, within the next working day, and over the following 30 days?
Your response must reconcile all relevant practice policies, identify the responsible clinical and administrative decision-makers, distinguish the mother's rights from the child's confidentiality rights, address any immediate safeguarding and clinical risks, and establish whether this incident requires a significant event analysis, formal complaint investigation, information governance incident assessment, or Duty of Candour consideration.
Where policies conflict, are expired, incomplete or ambiguous, explain how the conflict should be resolved without inventing authority or allowing administrative staff to make clinical decisions.
""".strip()

PARAPHRASES = [
    (
        "A mother is demanding her 14-year-old daughter's medical records. "
        "There is a confidential safeguarding note she asked not to share. "
        "The duty GP has left and the callback has not happened. She threatens a complaint. "
        "What precise actions must we take before closing if some policies are expired?"
    ),
    (
        "Proxy access may show a child's confidential consultation, the safeguarding concern "
        "was not escalated, and the mother will make a formal complaint. What should the "
        "practice manager do today? Mention significant events, information governance and "
        "duty of candour, and say if a policy is expired."
    ),
]

EXPECTED = {
    "sg",
    "conf",
    "gillick",
    "records",
    "proxy",
    "complaints",
    "sea",
    "ig",
    "candour",
    "duty",
}


def _inventory():
    return [
        Policy(
            "sg",
            "Safeguarding children",
            review_date="2020-01-01",
            owners=["Safeguarding lead"],
            tags=["Clinical"],
        ),
        Policy(
            "conf", "Confidentiality", review_date="2027-06-01", tags=["Information governance"]
        ),
        Policy(
            "gillick",
            "Gillick competence",
            review_date="2027-06-01",
            tags=["Young people", "Confidentiality"],
        ),
        Policy(
            "records",
            "Access to medical records",
            review_date="2027-06-01",
            tags=["Subject access"],
        ),
        Policy("proxy", "Proxy access to online records", review_date="2027-06-01"),
        Policy("complaints", "Complaints", review_date="2027-06-01", owners=["Practice manager"]),
        Policy("sea", "Significant events", review_date="2027-06-01"),
        Policy("ig", "Information governance incidents", review_date="2026-01-01"),
        Policy("candour", "Duty of Candour", review_date="2027-06-01"),
        Policy("duty", "Duty doctor and urgent callbacks", review_date="2027-06-01"),
        Policy("fire", "Fire safety", review_date="2027-06-01"),
        Policy("cold", "Cold chain", review_date="2027-06-01"),
    ]


class Sources:
    def __init__(self):
        self.policies = _inventory()
        self.reads = []

    def profile(self):
        return Document("profile", "Surgery Profile", "Practice manager: Jan du Plessis")

    def inventory(self):
        return self.policies

    def read(self, policy):
        self.reads.append(policy.id)
        return Document(
            policy.id,
            policy.title,
            "# Immediate actions\nEscalate to the duty clinician before closing.",
        )


class Model:
    def __init__(self):
        self.documents = []

    def select(self, question, history, inventory):
        return {"operation": "answer", "policy_ids": [], "resolved_question": question}

    def respond(self, question, history, today, profile, policies, documents, exhaustive, **kwargs):
        self.documents = documents
        if sum(len(document.text) for document in documents) > 350_000:
            raise AssertionError("scenario prompt exceeded the size budget")
        return {
            "answer": (
                "Before closing, escalate the safeguarding concern to a clinician. "
                "Do not give the mother the confidential consultation."
            ),
            "citations": [{"policy_id": "complaints", "section": "Immediate actions"}],
        }


def test_the_scenario_and_paraphrases_are_not_overdue_or_register_lists():
    assert not is_overdue_question(SCENARIO)
    assert not is_register_question(SCENARIO)
    for question in PARAPHRASES:
        assert not is_overdue_question(question), question
        assert not is_register_question(question), question
    assert is_overdue_question("which policies are overdue")
    assert is_overdue_question("list all overdue policies")


def test_scenario_retrieval_finds_the_relevant_policies_only():
    matches = lexical_policy_ids(SCENARIO, _inventory())
    assert EXPECTED <= set(matches)
    assert "fire" not in matches
    assert "cold" not in matches
    assert len(matches) <= 15
    for question in PARAPHRASES:
        found = set(lexical_policy_ids(question, _inventory()))
        assert {"sg", "complaints", "conf"} <= found
        assert "fire" not in found


def test_scenario_is_answered_from_policy_text_and_review_status():
    sources = Sources()
    model = Model()
    result = Agent(sources, model, clock=lambda: date(2026, 11, 25)).ask(SCENARIO, [])
    assert "escalate the safeguarding concern" in result.text.casefold()
    assert "Policy text was not read" not in result.text
    assert "The requested policy scope is too large" not in result.text
    assert "**Overdue policies**" not in result.text
    assert set(sources.reads) >= EXPECTED
    assert "Safeguarding children" in result.text
    assert "Overdue (review date 2020-01-01)" in result.text
    assert "Information governance incidents" in result.text
    assert "Overdue (review date 2026-01-01)" in result.text
    assert "This is not a list of every policy." in result.text
    assert "Fire safety" not in result.text
    assert model.documents


def test_a_large_multi_policy_prompt_shrinks_instead_of_rejecting_the_question():
    documents = [
        {
            "id": f"P{index}",
            "title": f"Policy {index}",
            "text": "word " * 30_000,
            "text_scope": "full",
        }
        for index in range(8)
    ]
    payload = {
        "question": "x" * 2000,
        "documents": documents,
        "policies": [{"id": f"P{index}", "title": f"Policy {index}"} for index in range(8)],
    }
    shown = {f"id{index}": documents[index]["text"] for index in range(8)}
    alias_to_id = {f"P{index}": f"id{index}" for index in range(8)}
    fitted = fit_response_payload(payload, shown, alias_to_id, limit=40_000)
    encoded = __import__("json").dumps(fitted)
    assert len(encoded) <= 40_000
    assert fitted["documents"]
    assert len(fitted["documents"]) < 8
    assert "Further text omitted" in fitted["documents"][0]["text"]
