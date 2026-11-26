from datetime import date

from nathan.agent import Agent
from nathan.domain import Document, Policy
from nathan.policy_index import PolicyIndex
from nathan.progress import Progress, use_progress


class Sources:
    def __init__(self):
        self.policies = [Policy("p", "Safety", review_date="2027-01-01")]

    def profile(self):
        return Document("profile", "Profile", "Lead: Test person")

    def inventory(self):
        return self.policies

    def read(self, policy):
        return Document(policy.id, policy.title, "1. Safety instructions")


class Model:
    def select(self, question, history, inventory):
        return {"operation": "answer", "policy_ids": ["p"], "resolved_question": question}

    def respond(self, question, history, today, profile, policies, documents, exhaustive):
        return {
            "answer": "Follow safety instructions.",
            "citations": [{"policy_id": "p", "section": "1"}],
        }


def _lines(events):
    return [event[1] for event in events]


def test_progress_records_elapsed_time_and_the_step_label():
    clock = {"now": 10.0}

    def now():
        return clock["now"]

    seen = []
    progress = Progress(on_event=seen.append, now=now)
    clock["now"] = 10.4
    progress.emit("Searching index: full-text", "Full-text search: 3 hits.")
    assert seen[0][0] == "Searching index: full-text"
    assert "+0.40s" in seen[0][1]
    assert "Full-text search: 3 hits." in seen[0][1]
    assert "sk-or-secret" not in seen[0][1]


def test_overdue_route_explains_itself_and_does_not_call_the_model():
    events = []

    class Unused(Model):
        def select(self, *args):
            raise AssertionError("overdue questions do not select")

        def respond(self, *args):
            raise AssertionError("overdue questions do not answer with the model")

    sources = Sources()
    sources.policies = [
        Policy("old", "Cold chain", review_date="2020-01-01"),
        Policy("live", "Infection control", review_date="2027-06-01"),
    ]
    Agent(sources, Unused(), clock=lambda: date(2026, 11, 25), on_event=events.append).ask(
        "Which policies are overdue?", []
    )
    lines = _lines(events)
    assert any("Routing: overdue list" in line for line in lines)
    assert any("List Overdue Policies" in line for line in lines)
    assert any("Summary:" in line for line in lines)
    assert not any("Calling the model" in line for line in lines)
    assert not any("Read Notion:" in line for line in lines)


def test_normal_answer_emits_retrieval_read_review_and_sources():
    events = []
    Agent(Sources(), Model(), clock=lambda: date(2026, 11, 25), on_event=events.append).ask(
        "What should I do about safety?", []
    )
    lines = _lines(events)
    assert any("Routing: normal retrieval" in line for line in lines)
    assert any("Title and tag matches:" in line for line in lines)
    assert any("Index search skipped" in line for line in lines)
    assert any("Read Notion: Safety" in line for line in lines)
    assert any("Live check:" in line for line in lines)
    assert any("Review status: Safety Live" in line for line in lines)
    assert any("Finished in" in line and "Sources:" in line for line in lines)
    assert any("Summary:" in line for line in lines)


def test_index_search_emits_each_stage_without_chunk_text():
    events = []

    class Embedder:
        model = "openai/text-embedding-3-small"
        last_error = ""

        def embed_one(self, text):
            return [0.1, 0.2]

    class Store:
        def search_vector(self, embedding, match_count, live_only, embedding_model=""):
            return [
                {
                    "page_id": "a",
                    "title": "Cold chain",
                    "section_heading": "Fridge",
                    "chunk_text": "SECRET body about the fridge",
                    "chunk_index": 0,
                    "is_live": True,
                    "similarity": 0.82,
                }
            ]

        def search_fts(self, query_text, match_count, live_only):
            return [
                {
                    "page_id": "b",
                    "title": "Complaints",
                    "section_heading": "Steps",
                    "chunk_text": "SECRET complaint text",
                    "chunk_index": 0,
                    "is_live": True,
                }
            ]

        def neighbours(self, page_id, chunk_index):
            if page_id != "a":
                return []
            return [
                {
                    "page_id": "a",
                    "title": "Cold chain",
                    "section_heading": "Next",
                    "chunk_text": "SECRET neighbour",
                    "chunk_index": 1,
                    "is_live": True,
                }
            ]

    index = PolicyIndex.__new__(PolicyIndex)
    index._embedder = Embedder()
    index._store = Store()
    progress = Progress(on_event=events.append)
    with use_progress(progress):
        hits = index.search("fridge", keyword_ids=["b", "a"])
    lines = _lines(events)
    joined = " ".join(lines)
    assert any(
        "Query embedding: OpenRouter openai/text-embedding-3-small" in line for line in lines
    )
    assert any("Vector search: 1 hits, top similarity 0.820" in line for line in lines)
    assert any("Full-text search: 1 hits" in line for line in lines)
    assert any("Fusion:" in line and "Cold chain" in line for line in lines)
    assert any("Neighbour chunks: added 1" in line for line in lines)
    assert "SECRET" not in joined
    assert hits
