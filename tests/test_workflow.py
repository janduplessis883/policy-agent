from datetime import date

import pytest

from nathan.agent import Agent
from nathan.domain import Document, Policy, SourceError
from nathan.tools.create_placeholder import create_placeholder


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


def test_profile_failure_stops_before_inventory():
    sources = Sources(fail=True)
    with pytest.raises(SourceError):
        Agent(sources, Model()).ask("Question", [])
    assert sources.calls == ["profile"]


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
    created = create_placeholder(client, "New policy", date(2026, 11, 16), [])
    assert set(client.payload["properties"]) == {"Policy", "Tags", "Review_Date"}
    assert client.payload.get("children", []) == []
    assert "template" not in client.payload
    assert created["id"] == "new"


def test_creation_blocks_duplicate_title():
    with pytest.raises(SourceError):
        create_placeholder(None, "  SAFETY ", date.today(), [Policy(id="p", title="Safety")])


def test_gap_reads_every_policy_and_rejects_incomplete_evidence():
    class GapModel(Model):
        def select(self, *args):
            return {"operation": "gap", "policy_ids": []}

        def respond(self, *args):
            return {
                "answer": "Missing coverage.",
                "citations": [],
                "classification": "Confirmed gap",
                "placeholder_title": "New policy",
            }

    sources = Sources()
    sources.policies.append(Policy(id="q", title="Other"))

    def read(policy):
        sources.calls.append("read:" + policy.id)
        return Document(id=policy.id, title=policy.title, text="Text", complete=False)

    sources.read = read
    with pytest.raises(SourceError, match="fully verified"):
        Agent(sources, GapModel()).ask("Is coverage missing?", [])
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


def test_creation_revalidates_and_refreshes_inventory():
    class GapModel(Model):
        def respond(self, *args):
            return {
                "answer": "Gap confirmed.",
                "citations": [],
                "classification": "Confirmed gap",
                "placeholder_title": "New policy",
            }

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
    text = Agent(sources, GapModel()).create("New policy", "Need this policy", []).text
    assert sources.calls == ["profile", "inventory", "read", "inventory"]
    assert text.startswith("Created New policy")


def test_creation_stops_if_policy_content_changed():
    from dataclasses import replace

    class GapModel(Model):
        def respond(self, *args):
            return {
                "answer": "Gap confirmed.",
                "citations": [],
                "classification": "Confirmed gap",
                "placeholder_title": "New policy",
            }

    sources = Sources()
    original_inventory = sources.inventory

    def inventory():
        policies = original_inventory()
        if sources.calls.count("inventory") == 2:
            return [replace(policies[0], last_edited_time="changed")]
        return policies

    sources.inventory = inventory
    with pytest.raises(SourceError, match="inventory changed"):
        Agent(sources, GapModel()).create("New policy", "Need written coverage", [])


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
