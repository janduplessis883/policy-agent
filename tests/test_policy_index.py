import logging
from datetime import date

import httpx

from nathan.agent import Agent
from nathan.config import Settings
from nathan.domain import Document, Policy, SourceError
from nathan.policy_index import ChunkHit, PolicyIndex, build_index
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


def _index_with(policies, embed):
    saved = []

    class Store:
        def try_lock(self, holder, seconds=300):
            return True

        def unlock(self, holder):
            self.unlocked = True

        def fetch_state(self):
            return []

        def delete_pages(self, ids):
            self.deleted = list(ids)

        def replace_page(self, page_id, chunks):
            saved.append((page_id, chunks))

        def touch_page(self, *args):
            pass

    index = PolicyIndex.__new__(PolicyIndex)
    index._store = Store()
    index._notion = object()
    index._embedder = embed
    index._holder = "test"
    index.degraded = False
    index.saved = saved
    return index


class _Vectors:
    model = "text-embedding-3-small"
    dimensions = 1536

    def embed(self, texts):
        return [[0.1] * 1536 for _ in texts]


class _NoVectors:
    model = "text-embedding-3-small"
    dimensions = 1536

    def embed(self, texts):
        return None


def _patch_sources(monkeypatch, policies, fail_ids=()):
    from nathan.domain import Document

    monkeypatch.setattr("nathan.tools.list_policies.list_policies", lambda client: policies)

    def read(client, policy):
        if policy.id in fail_ids:
            raise SourceError("Notion could not read this page")
        return Document(
            policy.id, policy.title, "# Fridge temperatures\nRecord the minimum every day."
        )

    monkeypatch.setattr("nathan.tools.read_policy.read_policy", read)


def test_max_pages_none_indexes_every_page(monkeypatch):
    policies = [
        Policy(f"p{i}", f"Policy {i}", review_date="2027-01-01", last_edited_time="t")
        for i in range(3)
    ]
    _patch_sources(monkeypatch, policies)
    index = _index_with(policies, _Vectors())
    text = index.sync_incremental(max_pages=None)
    assert "Notion inventory: 3 pages" in text
    assert "Embedded 3" in text
    assert [page_id for page_id, _ in index.saved] == ["p0", "p1", "p2"]


def test_embedding_failure_is_counted_and_text_is_still_stored(monkeypatch):
    policies = [
        Policy("p0", "Cold chain", review_date="2027-01-01"),
        Policy("p1", "Safeguarding", review_date="2027-01-01"),
    ]
    _patch_sources(monkeypatch, policies)
    index = _index_with(policies, _NoVectors())
    text = index.sync_incremental(max_pages=None)
    assert "Notion inventory: 2 pages" in text
    assert "stored without embeddings 2" in text
    assert "Embedding failed" in text
    assert all(chunk["embedding"] == "" for _, chunks in index.saved for chunk in chunks)
    assert all(chunk["embedding_model"] is None for _, chunks in index.saved for chunk in chunks)
    assert "p0: embedding failed (no vectors returned)" in text


def test_read_errors_do_not_hide_the_inventory_count(monkeypatch):
    policies = [Policy("bad", "Broken", review_date="2027-01-01")]
    _patch_sources(monkeypatch, policies, fail_ids={"bad"})
    text = _index_with(policies, _Vectors()).sync_incremental(max_pages=None)
    assert "Notion inventory: 1 page " in text
    assert "read failed 1" in text
    assert "bad: read failed (SourceError)" in text


def test_max_pages_stops_and_reports_the_remainder(monkeypatch):
    policies = [Policy(f"p{i}", f"Policy {i}", review_date="2027-01-01") for i in range(3)]
    _patch_sources(monkeypatch, policies)
    text = _index_with(policies, _Vectors()).sync_incremental(max_pages=1)
    assert "Embedded 1" in text
    assert "2 changed pages are still waiting" in text


def test_excluded_pages_explain_a_zero_refresh(monkeypatch):
    policies = [Policy("draft", "Draft policy", excluded=True)]
    _patch_sources(monkeypatch, policies)
    text = _index_with(policies, _Vectors()).sync_incremental(max_pages=None)
    assert "Notion inventory: 1 page " in text
    assert "1 excluded as archived or draft status" in text
    assert "Embedded 0" in text


def test_query_search_uses_the_same_embedding_model():
    class Store:
        def search_vector(self, embedding, match_count, live_only, embedding_model=""):
            self.model = embedding_model
            self.embedding = list(embedding)
            return [
                {
                    "page_id": "p",
                    "title": "Cold chain",
                    "section_heading": "Fridge",
                    "chunk_text": "Record the minimum.",
                    "chunk_index": 0,
                    "is_live": True,
                }
            ]

        def search_fts(self, *args):
            return []

        def neighbours(self, *args):
            return []

    class Emb:
        model = "openai/text-embedding-3-small"

        def embed_one(self, text):
            return [0.25]

    index = PolicyIndex.__new__(PolicyIndex)
    index._store = Store()
    index._embedder = Emb()
    hits = index.search("fridge temperatures")
    assert index._store.model == "openai/text-embedding-3-small"
    assert index._store.embedding == [0.25]
    assert hits[0].page_id == "p"


def test_vector_query_sends_the_model_to_supabase():
    import json

    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[])

    store = SupabaseStore(
        "https://example.supabase.co",
        "sb_secret_example",
        transport=httpx.MockTransport(handler),
    )
    store.search_vector([0.1, 0.2], 5, True, "openai/text-embedding-3-small")
    assert seen["body"]["p_embedding_model"] == "openai/text-embedding-3-small"
    assert seen["body"]["live_only"] is True
    store.close()


def test_lock_already_held_does_not_look_like_an_empty_inventory():
    index = _index_with([], _Vectors())

    def busy(holder, seconds=300):
        return False

    index._store.try_lock = busy
    assert index.sync_incremental(max_pages=None) == "Another index update is already running."


def test_supabase_http_error_logs_the_postgrest_body_not_the_key(caplog):
    seen = {}

    def handler(request):
        seen["apikey"] = request.headers.get("apikey")
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            403,
            json={
                "code": "42501",
                "message": "permission denied for function nathan_try_index_lock",
                "hint": "Grant execute to service_role. Key was sb_secret_shouldnotappear",
            },
        )

    store = SupabaseStore(
        "https://example.supabase.co",
        "sb_secret_shouldnotappear",
        transport=httpx.MockTransport(handler),
    )
    with caplog.at_level(logging.WARNING):
        try:
            store.search_fts("fridge", 5, True)
            raised = False
        except IndexUnavailable as exc:
            raised = True
            assert "sb_secret_shouldnotappear" not in str(exc)
    assert raised
    assert seen["apikey"] == "sb_secret_shouldnotappear"
    assert seen["authorization"] is None
    assert "42501" in caplog.text
    assert "permission denied for function nathan_try_index_lock" in caplog.text
    assert "sb_secret_shouldnotappear" not in caplog.text
    store.close()


def test_legacy_service_role_jwt_is_sent_as_bearer_and_apikey():
    from nathan.supabase_store import supabase_headers

    legacy = _jwt({"role": "service_role"})
    headers = supabase_headers(legacy)
    assert headers["apikey"] == legacy
    assert headers["Authorization"] == f"Bearer {legacy}"


def test_publishable_key_is_not_a_bearer_token_and_is_warned(caplog):
    from nathan.supabase_store import supabase_headers

    with caplog.at_level(logging.WARNING):
        headers = supabase_headers("sb_publishable_example")
    assert headers["apikey"] == "sb_publishable_example"
    assert "Authorization" not in headers
    assert "publishable" in caplog.text
    assert "sb_publishable_example" not in caplog.text


def _jwt(payload: dict) -> str:
    import base64
    import json

    def part(value):
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part(payload)}.signature"
