import logging
from datetime import date

import httpx

from nathan.agent import Agent
from nathan.config import Settings
from nathan.domain import Document, Policy, SourceError
from nathan.policy_index import ChunkHit, build_index
from nathan.supabase_store import IndexUnavailable, SupabaseStore


class Sources:
    def __init__(self):
        self.calls = []
        self.policies = [
            Policy(f"p{i}", f"Policy {i}", review_date="2027-01-01") for i in range(20)
        ]

    def profile(self):
        self.calls.append("profile")
        return Document("profile", "Profile", "Lead: Test person")

    def inventory(self):
        self.calls.append("inventory")
        return self.policies

    def read(self, policy):
        self.calls.append("read:" + policy.id)
        raise AssertionError("indexed policies must not be read in full")


class Model:
    def select(self, question, history, inventory):
        return {"operation": "answer", "policy_ids": [inventory[0].id]}

    def respond(self, question, history, today, profile, policies, documents, exhaustive):
        assert len(documents) > 15
        assert all(document.indexed for document in documents)
        assert not exhaustive
        return {
            "answer": "Record the minimum every day.",
            "citations": [{"policy_id": "p0", "section": "Fridge temperatures"}],
        }


class WideIndex:
    enabled = True
    degraded = False

    def search(self, question, keyword_ids=(), live_only=True):
        return [
            ChunkHit(
                f"p{i}",
                f"Policy {i}",
                "Fridge temperatures",
                "Record the minimum every day.",
                0,
                True,
            )
            for i in range(20)
        ]


def test_indexed_answer_uses_more_than_fifteen_policies_without_full_reads():
    result = Agent(Sources(), Model(), clock=lambda: date(2026, 11, 25), index=WideIndex()).ask(
        "Where is the fridge minimum recorded?", []
    )
    assert "too large" not in result.text
    assert "limited to 15" not in result.text
    assert "Indexed sections" in result.text
    assert "Policy 0" in result.text
    assert "(Live)" in result.text


def test_non_live_indexed_procedure_is_withheld_and_labelled():
    class One(Sources):
        def __init__(self):
            self.calls = []
            self.policies = [Policy("p", "Old policy", review_date="2020-01-01")]

        def read(self, policy):
            raise AssertionError("the index already supplied this page")

    class Index:
        enabled = True
        degraded = False

        def search(self, question, keyword_ids=(), live_only=True):
            assert live_only is True
            return [ChunkHit("p", "Old policy", "Archive", "Secret procedure text", 0, False)]

    class Quiet(Model):
        def select(self, question, history, inventory):
            return {"operation": "answer", "policy_ids": ["p"]}

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert "Secret procedure" not in documents[0].text
            assert "Not Live" in documents[0].text
            return {
                "answer": "No live procedure is available.",
                "citations": [{"policy_id": "p", "section": "not stated"}],
            }

    result = Agent(One(), Quiet(), clock=lambda: date(2026, 11, 25), index=Index()).ask(
        "What does the old document require?", []
    )
    assert "(Not Live)" in result.text
    assert "Secret procedure" not in result.text


def test_index_failure_falls_back_to_keyword_reads():
    class SourcesWithRead(Sources):
        def read(self, policy):
            self.calls.append("read")
            return Document(policy.id, policy.title, "# Duties\nDo the documented step.")

    class Broken:
        def __init__(self):
            self.enabled = True
            self.degraded = False

        def search(self, *args, **kwargs):
            raise SourceError("index down")

    class Narrow(Model):
        def select(self, question, history, inventory):
            return {"operation": "answer", "policy_ids": []}

        def respond(self, question, history, today, profile, policies, documents, exhaustive):
            assert documents
            assert not any(getattr(document, "indexed", False) for document in documents)
            return {"answer": "Do the documented step.", "citations": []}

    sources = SourcesWithRead()
    sources.policies = [Policy("only", "Cold chain", review_date="2027-01-01")]
    index = Broken()
    result = Agent(sources, Narrow(), clock=lambda: date(2026, 11, 25), index=index).ask(
        "What is our cold chain procedure?", []
    )
    assert "read" in sources.calls
    assert "Indexed sections" not in result.text
    assert index.degraded is True


def test_missing_supabase_settings_stay_in_keyword_mode(caplog):
    settings = Settings("super-secret-value", "token", "pin")
    assert settings.index_enabled is False
    with caplog.at_level(logging.WARNING):
        index = build_index(settings, notion=None)
    assert index.enabled is False
    assert "keyword mode" in caplog.text
    assert "super-secret-value" not in caplog.text


def test_supabase_http_error_does_not_log_the_service_key(caplog):
    def handler(request):
        return httpx.Response(401, json={"message": "bad key"})

    store = SupabaseStore(
        "https://example.supabase.co",
        "service-role-secret-value",
        transport=httpx.MockTransport(handler),
    )
    with caplog.at_level(logging.WARNING):
        try:
            store.search_fts("fridge", 5, True)
            raised = False
        except IndexUnavailable as exc:
            raised = True
            assert "service-role-secret-value" not in str(exc)
    assert raised
    assert "service-role-secret-value" not in caplog.text
    store.close()
