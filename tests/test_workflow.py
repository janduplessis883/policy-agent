from datetime import date

import pytest

from nathan.agent import Agent
from nathan.domain import Document, Policy, SourceError
from nathan.tools.create_placeholder import create_placeholder
from nathan.tools.log_answer import log_answer


class Sources:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.policies = [Policy(id="p", title="Safety", review_date="2027-01-01")]

    def profile(self):
        self.calls.append("profile")
        if self.fail:
            raise SourceError("Profile unavailable")
        return Document(id="profile", title="Profile", text="Lead: Test person")

    def inventory(self):
        self.calls.append("inventory")
        return self.policies

    def read(self, policy):
        self.calls.append("read")
        return Document(id=policy.id, title=policy.title, text="1. Safety instructions")


class Model:
    def select(self, question, history, inventory):
        return {"operation": "answer", "policy_ids": ["p"]}

    def respond(self, question, history, today, profile, policies, documents, exhaustive):
        return {
            "answer": "Follow safety instructions.",
            "citations": [{"policy_id": "p", "section": "1"}],
            "classification": None,
            "placeholder_title": None,
        }


def test_mandatory_sequence():
    sources = Sources()

    def clock():
        sources.calls.append("date")
        return date(2026, 11, 16)

    agent = Agent(sources, Model(), clock=clock)
    result = agent.ask("What should I do?", [])
    assert sources.calls == ["date", "profile", "inventory", "read"]
    assert "Safety" in result.text
    assert "✓ Read in full" in result.text


def test_profile_failure_stops_before_inventory():
    sources = Sources(fail=True)
    with pytest.raises(SourceError):
        Agent(sources, Model()).ask("Question", [])
    assert sources.calls == ["profile"]


@pytest.mark.parametrize("save_fails", [False, True])
def test_logs_final_answer_without_losing_it_if_notion_fails(save_fails):
    class Client:
        def request(self, method, path, **kwargs):
            requests.append((method, path, kwargs))
            if save_fails:
                raise SourceError("Notion unavailable")
            return {"id": "log-page"}

    requests = []
    events = []
    model = Model()
    model.calls = [("selection-model", 200), ("answer-model", 300)]
    agent = Agent(
        Sources(),
        model,
        on_event=events.append,
        log_answer=lambda *args: log_answer(Client(), *args),
    )

    result = agent.ask("What is the safety policy?", [])

    [(method, path, kwargs)] = requests
    assert (method, path, kwargs["write"]) == ("POST", "pages", True)
    payload = kwargs["json"]
    assert payload["parent"]["data_source_id"] == "41dfdfd6-8a97-80f2-addc-000bae8d9225"
    assert payload["markdown"] == result.text
    assert "✓ Read in full" in payload["markdown"]
    props = payload["properties"]
    assert props["Question?"]["title"][0]["text"]["content"] == "What is the safety policy?"
    assert "T" in props["Date"]["date"]["start"]
    assert props["Model"]["rich_text"][0]["text"]["content"] == "selection-model, answer-model"
    assert props["Tokens used"]["number"] == 500
    assert events[-1] == (
        "Log Answer · Notion did not confirm the save"
        if save_fails
        else "Log Answer · saved to Notion"
    )


def test_unknown_policy_reference_is_rejected():
    class BadModel(Model):
        def select(self, *args):
            return {"operation": "answer", "policy_ids": ["invented"]}

    with pytest.raises(SourceError):
        Agent(Sources(), BadModel()).ask("Question", [])


def test_creation_sends_exact_fields_and_no_template():
    class Client:
        def __init__(self):
            self.payload = None

        def request(self, method, path, **kwargs):
            self.payload = kwargs["json"]
            return {"id": "new", "public_url": None, "properties": self.payload["properties"]}

        def children(self, page):
            return []

        def page(self, page):
            return {"id": "new", "properties": self.payload["properties"]}

    client = Client()
    created = create_placeholder(
        client, "New policy", date(2026, 11, 16), [], "Define the required procedure."
    )
    assert set(client.payload["properties"]) == {"Policy", "Tags", "Review_Date", "Agent Advice"}
    assert client.payload.get("children", []) == []
    assert "template" not in client.payload
    assert created["id"] == "new"


def test_creation_blocks_duplicate_title():
    with pytest.raises(SourceError):
        create_placeholder(
            None, "  SAFETY ", date.today(), [Policy(id="p", title="Safety")], "Advice"
        )


def test_gap_reads_selected_policies_and_downgrades_incomplete_evidence():
    class GapModel(Model):
        def select(self, *args):
            return {"operation": "gap", "policy_ids": [p.id for p in sources.policies]}

        def respond(self, *args):
            return {
                "answer": "Missing coverage.",
                "citations": [{"policy_id": "p", "section": "1"}],
                "classification": "Confirmed gap",
                "placeholder_title": "New policy",
            }

    sources = Sources()
    sources.policies.append(Policy(id="q", title="Other"))

    def read(policy):
        sources.calls.append("read:" + policy.id)
        return Document(id=policy.id, title=policy.title, text="Text", complete=False)

    sources.read = read
    result = Agent(sources, GapModel()).ask("Is coverage missing?", [])
    assert "Potential gap" in result.text
    assert "could not be read" in result.text
    assert "⚠ Incomplete read" in result.text
    assert "- Safety\n- Other" in result.text
    assert result.proposal is None
    assert sources.calls == ["profile", "inventory", "read:p", "read:q"]


def test_non_live_procedures_are_withheld_from_answer_model():
    class InspectModel(Model):
        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert "Safety instructions" not in documents[0].text
            assert "Not Live" in documents[0].text
            return {
                "answer": "No Live policy is available.",
                "citations": [],
                "classification": None,
                "placeholder_title": None,
            }

    sources = Sources()
    sources.policies[0].review_date = "2020-01-01"
    assert "No Live" in Agent(sources, InspectModel()).ask("What do I do?", []).text


def test_authorised_creation_uses_saved_inventory_and_advice_without_reloading():
    class Client:
        def request(self, method, path, **kwargs):
            self.properties = kwargs["json"]["properties"]
            return {"id": "new"}

        def page(self, page):
            return {"properties": self.properties}

        def children(self, page):
            return []

    sources = Sources()
    sources.client = Client()
    advice = "Define ECG recording, interpretation, escalation and staff training."
    text = Agent(sources, Model()).create("ECG Policy", advice, sources.policies).text
    assert sources.calls == []
    assert sources.client.properties["Agent Advice"]["rich_text"][0]["text"]["content"] == advice
    assert text.startswith("Created ECG Policy")


def test_creation_checks_duplicates_against_saved_inventory():
    sources = Sources()
    sources.client = None
    with pytest.raises(SourceError, match="existing record"):
        Agent(sources, Model()).create("Safety", "Define safety procedures.", sources.policies)
    assert sources.calls == []


def test_staff_answer_can_cite_retrieved_surgery_profile():
    class StaffModel(Model):
        def select(self, *args):
            return {"operation": "answer", "policy_ids": []}

        def respond(self, *args):
            return {
                "answer": "The recorded lead is Test person.",
                "citations": [{"policy_id": "profile", "section": "Clinical leads"}],
                "classification": None,
                "placeholder_title": None,
            }

    result = Agent(Sources(), StaffModel()).ask("Who is the end of life lead?", [])
    assert "Profile" in result.text
    assert "Not Live" not in result.text


def test_reports_actual_tool_order_to_ui():
    events = []
    Agent(Sources(), Model(), on_event=events.append).ask("Question", [])
    assert events == [
        "Current Date · Europe/London",
        "Get Surgery Profile",
        "List Policies",
        "Read Policy · Safety",
    ]


@pytest.mark.parametrize("operation", ["answer", "review", "gap", "placeholder"])
def test_policy_reads_are_capped_at_fifteen(operation):
    sources = Sources()
    sources.policies = [Policy(id=f"p{i}", title=f"Policy {i}") for i in range(25)]

    class BroadModel(Model):
        def select(self, *args):
            return {"operation": operation, "policy_ids": [p.id for p in sources.policies]}

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert len(documents) == 15
            assert not exhaustive
            return {"answer": "Limited review.", "citations": []}

    result = Agent(sources, BroadModel()).ask("Review everything", [])
    assert sources.calls.count("read") == 15
    assert "15" in result.text


def test_exactly_fifteen_policies_can_still_be_exhaustive():
    sources = Sources()
    sources.policies = [Policy(id=f"p{i}", title=f"Policy {i}") for i in range(15)]

    class GapModel(Model):
        def select(self, *args):
            return {"operation": "gap", "policy_ids": [p.id for p in sources.policies]}

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert len(documents) == 15
            assert exhaustive
            return {"answer": "Reviewed all policies.", "citations": []}

    Agent(sources, GapModel()).ask("Review everything", [])
    assert sources.calls.count("read") == 15


def test_ecg_placeholder_offer_does_not_read_unrelated_policies():
    sources = Sources()
    sources.policies = [Policy(id=f"p{i}", title=f"Unrelated {i}") for i in range(30)]

    class ECGModel(Model):
        def select(self, *args):
            return {"operation": "placeholder", "policy_ids": []}

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert documents == []
            assert not exhaustive
            return {
                "answer": "ECG coverage was not found; collection coverage is unverified.",
                "citations": [],
                "classification": "Potential gap",
                "placeholder_title": "ECG Policy",
                "placeholder_advice": "Define ECG recording and escalation.",
            }

    result = Agent(sources, ECGModel()).ask("Create a placeholder policy on ECGs", [])
    assert sources.calls == ["profile", "inventory"]
    assert result.proposal == "ECG Policy"
    assert "Create placeholder" in result.text


@pytest.mark.parametrize("complete", [True, False])
def test_proposal_preserves_advice_and_inventory_only_after_complete_reads(complete):
    sources = Sources()

    class ECGModel(Model):
        def select(self, *args):
            return {"operation": "placeholder", "policy_ids": ["p"]}

        def respond(self, *args):
            return {
                "answer": "ECG coverage not found.",
                "citations": [],
                "classification": "Potential gap",
                "placeholder_title": "ECG Policy",
                "placeholder_advice": "Define ECG recording and escalation.",
            }

    sources.read = lambda p: Document(p.id, p.title, "Text", complete=complete)
    result = Agent(sources, ECGModel()).ask("Create ECG policy", [])
    if complete:
        assert result.proposal == "ECG Policy"
        assert result.proposal_advice == "Define ECG recording and escalation."
        assert result.proposal_inventory == sources.policies
    else:
        assert result.proposal is None


def test_partial_confirmed_gap_is_downgraded_and_retains_placeholder_offer():
    class GapModel(Model):
        def respond(self, *args):
            return {
                "answer": "No policy exists anywhere.",
                "citations": [],
                "classification": "Confirmed gap",
                "placeholder_title": "New policy",
                "placeholder_advice": "Define the required procedure.",
            }

    sources = Sources()
    sources.policies.append(Policy("unrelated", "Other"))
    result = Agent(sources, GapModel()).ask("Need written coverage", [])
    assert "Potential gap" in result.text
    assert "not reviewed" in result.text
    assert "No policy exists anywhere" not in result.text
    assert result.proposal == "New policy"


def test_title_tag_search_recovers_ecg_match_from_empty_model_selection():
    sources = Sources()
    sources.policies = [Policy("ecg", "Electrocardiography", review_date="2027-01-01")]

    class ECGModel(Model):
        def select(self, *args):
            return {"operation": "gap", "policy_ids": []}

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert [d.id for d in documents] == ["ecg"]
            return {"answer": "Existing ECG coverage found.", "citations": []}

    Agent(sources, ECGModel()).ask("Do we have an ECG policy?", [])
    assert sources.calls.count("read") == 1


def test_unreadable_relevant_policy_is_reported_without_a_placeholder():
    sources = Sources()

    def read(policy):
        raise SourceError("Not accessible")

    sources.read = read

    class GapModel(Model):
        def select(self, *args):
            return {"operation": "gap", "policy_ids": ["p"]}

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert not exhaustive
            assert not documents[0].complete
            return {
                "answer": "Missing.",
                "citations": [],
                "classification": "Confirmed gap",
                "placeholder_title": "New policy",
                "placeholder_advice": "Define procedure.",
            }

    result = Agent(sources, GapModel()).ask("Is coverage missing?", [])
    assert "Potential gap" in result.text
    assert "could not be read" in result.text
    assert "Safety" in result.text
    assert result.proposal is None


def test_unreadable_policy_cannot_support_a_content_citation():
    sources = Sources()
    sources.read = lambda p: Document(p.id, p.title, "", complete=False)
    with pytest.raises(SourceError, match="unverified policy citation"):
        Agent(sources, Model()).ask("Safety procedure", [])


def test_synonym_matches_respect_fifteen_policy_read_limit():
    sources = Sources()
    sources.policies = [Policy(f"p{i}", f"Electrocardiography {i}") for i in range(20)]

    class GapModel(Model):
        def select(self, *args):
            return {"operation": "gap", "policy_ids": []}

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert len(documents) == 15
            assert not exhaustive
            return {
                "answer": "Review limited.",
                "citations": [],
                "classification": "Confirmed gap",
                "placeholder_title": "ECG policy",
                "placeholder_advice": "Define recording and escalation.",
            }

    result = Agent(sources, GapModel()).ask("ECG coverage", [])
    assert sources.calls.count("read") == 15
    assert "Potential gap" in result.text
    assert "15" in result.text
    assert result.proposal is None


@pytest.mark.parametrize(
    "question,resolved,policy_id,title",
    [
        (
            "Who is responsible for that?",
            "Who is responsible for the cold chain procedure?",
            "cold",
            "Cold chain",
        ),
        (
            "What about out of hours?",
            "What is the cold chain procedure out of hours?",
            "cold",
            "Cold chain",
        ),
        (
            "Now tell me about complaints instead.",
            "What is our complaints procedure?",
            "complaints",
            "Complaints",
        ),
    ],
)
def test_followup_search_uses_resolved_topic_and_retains_it_for_next_turn(
    question,
    resolved,
    policy_id,
    title,
):
    sources = Sources()
    sources.policies = [
        Policy("cold", "Cold chain", review_date="2027-01-01"),
        Policy("complaints", "Complaints", review_date="2027-01-01"),
    ]

    class FollowupModel(Model):
        def select(self, question, history, inventory):
            return {
                "operation": "answer",
                "policy_ids": [],
                "resolved_question": resolved,
            }

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert question == resolved
            assert [d.id for d in documents] == [policy_id]
            return {
                "answer": "Follow the documented procedure.",
                "citations": [{"policy_id": policy_id, "section": "Responsibilities"}],
            }

    result = Agent(sources, FollowupModel()).ask(
        question,
        [
            {"role": "user", "content": "What is our cold chain procedure?"},
            {"role": "assistant", "content": "Follow the cold chain policy."},
        ],
    )
    assert title in result.text
    assert result.conversation_context == {
        "question": resolved,
        "policies": [{"id": policy_id, "title": title}],
    }


def test_unresolved_followup_still_reads_the_previous_policy():
    sources = Sources()
    sources.policies = [
        Policy("cold", "Cold chain", review_date="2027-01-01"),
        Policy("fire", "Fire safety", review_date="2027-01-01"),
    ]

    class Unresolved(Model):
        def select(self, question, history, inventory):
            return {"operation": "answer", "policy_ids": []}

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert [document.id for document in documents] == ["cold"]
            return {
                "answer": "The cold chain responsibilities are in the policy.",
                "citations": [{"policy_id": "cold", "section": "1"}],
            }

    result = Agent(sources, Unresolved()).ask(
        "Who is responsible for that?",
        [
            {"role": "user", "content": "What is our cold chain procedure?"},
            {"role": "assistant", "content": "Follow the cold chain policy."},
        ],
    )
    assert "Cold chain" in result.text
    assert "**Sources**" in result.text
    assert "Not covered by a cited policy section" not in result.text


def test_unrelated_question_does_not_reuse_the_previous_topic():
    sources = Sources()
    sources.policies = [Policy("cold", "Cold chain", review_date="2027-01-01")]

    class Weather(Model):
        def select(self, *args):
            return {"operation": "answer", "policy_ids": []}

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert documents == []
            return {"answer": "No weather procedure was retrieved.", "citations": []}

    result = Agent(sources, Weather()).ask(
        "What is the weather today?",
        [{"role": "user", "content": "What is our cold chain procedure?"}],
    )
    assert "Not covered by a cited policy section" in result.text
    assert "Cold chain" not in result.text


def test_missing_citation_section_is_labelled():
    class Invented(Model):
        def respond(self, *args):
            return {
                "answer": "Follow safety instructions.",
                "citations": [{"policy_id": "p", "section": "Imaginary section"}],
            }

    result = Agent(Sources(), Invented()).ask("What should I do?", [])
    assert "section not found in retrieved text" in result.text
    assert "Cited section not found" in result.text


def test_cited_answer_lists_sources_without_a_coverage_warning():
    result = Agent(Sources(), Model(), clock=lambda: date(2026, 11, 16)).ask(
        "What should I do?", []
    )
    assert "**Sources**" in result.text
    assert "Safety, 1 (Live) · ✓ Read in full" in result.text
    assert "Not covered by a cited policy section" not in result.text
    assert "section not found" not in result.text


def test_excerpted_policy_is_disclosed_on_the_answer():
    model = Model()
    model.excerpted_titles = ["Safety"]
    result = Agent(Sources(), model).ask("What should I do?", [])
    assert "Focused read" in result.text
    assert "Safety" in result.text


def test_unexpected_log_failure_keeps_the_answer():
    events = []

    def boom(*args, **kwargs):
        raise RuntimeError("token sk-secret should not surface")

    result = Agent(Sources(), Model(), on_event=events.append, log_answer=boom).ask(
        "What should I do?", []
    )
    assert "Safety" in result.text
    assert events[-1] == "Log Answer · Notion did not confirm the save"
    assert all("sk-secret" not in event for event in events)


def test_overdue_question_uses_the_register_and_does_not_call_the_model():
    sources = Sources()
    sources.policies = [
        Policy("old", "Cold chain", review_date="2020-01-01", owners=["Jan"]),
        Policy("live", "Infection control", review_date="2027-06-01"),
    ]

    class Unused(Model):
        def select(self, *args):
            raise AssertionError("register questions do not select policies")

        def respond(self, *args):
            raise AssertionError("register questions do not call the model")

    events = []
    result = Agent(sources, Unused(), clock=lambda: date(2026, 11, 25), on_event=events.append).ask(
        "Which policies are overdue?", []
    )
    assert sources.calls == ["profile", "inventory"]
    assert "Read" not in " ".join(events)
    assert "Cold chain" in result.text
    assert "review date 2020-01-01" in result.text
    assert "Infection control" not in result.text
    assert result.proposal is None
    assert result.source_note
    assert result.conversation_context["policies"] == []


def test_register_stops_when_the_profile_cannot_be_read():
    sources = Sources(fail=True)

    class Unused(Model):
        def select(self, *args):
            raise AssertionError("profile failure happens before the model")

    with pytest.raises(SourceError):
        Agent(sources, Unused()).ask("Which policies are overdue?", [])
    assert sources.calls == ["profile"]


def test_procedure_question_still_reads_a_policy():
    sources = Sources()
    Agent(sources, Model()).ask("What is our cold chain procedure?", [])
    assert "read" in sources.calls
