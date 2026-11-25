import json
from datetime import date

import httpx
import pytest
from openai import OpenAI

from nathan.config import Settings
from nathan.domain import Document, Policy
from nathan.model import PolicyModel


def test_compatible_endpoint_uses_auto_and_plain_chat_completions():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "auto",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"operation": "answer", "policy_ids": ["p"]}),
                        },
                    }
                ],
            },
        )

    model = PolicyModel(Settings("test", "test", "123456"))
    model.client.close()
    model.client = OpenAI(
        api_key="test",
        base_url=model.client.base_url,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    selection = model.select("Question", [], [Policy(id="p", title="Safety")])
    assert selection["policy_ids"] == ["p"]
    assert requests[0][0] == "/v1/chat/completions"
    assert requests[0][1]["model"] == "auto"
    assert "tools" not in requests[0][1]
    assert "response_format" not in requests[0][1]
    model.close()


def compatible_model(responses):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        content = responses[min(len(calls) - 1, len(responses) - 1)]
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "auto",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
            },
        )

    model = PolicyModel(Settings("test", "test", "123456"))
    model.client.close()
    model.client = OpenAI(
        api_key="test",
        base_url="https://example.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return model, calls


def test_selector_has_its_own_task_prompt_and_short_source_ids():
    model, calls = compatible_model(['{"operation":"answer","policy_ids":["P1"]}'])
    selected = model.select("Cold chain?", [], [Policy(id="real-page-id", title="Cold chain")])
    assert selected["policy_ids"] == ["real-page-id"]
    system = calls[0]["messages"][0]["content"]
    assert "Mandatory tool execution order" not in system
    assert "Do not call tools" in system
    model.close()


def test_fenced_json_is_accepted_without_retry():
    model, calls = compatible_model(['```json\n{"operation":"answer","policy_ids":[]}\n```'])
    assert model.select("Who is the lead?", [], [])["policy_ids"] == []
    assert len(calls) == 1
    model.close()


def test_invalid_tool_response_gets_specific_validation_feedback():
    model, calls = compatible_model(
        ['{"tool":"Get Surgery Profile"}', '{"operation":"answer","policy_ids":[]}']
    )
    model.select("Who is the lead?", [], [])
    repair = calls[1]["messages"][-1]["content"]
    assert "operation" in repair and "policy_ids" in repair
    assert "already" in repair
    model.close()


def test_streams_answer_text_and_maps_profile_citation():
    snapshots = []
    payloads = []
    response_parts = [
        '{"answer":"The recorded ',
        'lead is Test person.","citations":',
        '[{"policy_id":"S0","section":"Clinical leads"}]}',
    ]

    def handler(request):
        payloads.append(json.loads(request.content))
        chunks = []
        for part in response_parts:
            data = {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "routed-answer-model",
                "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}],
            }
            chunks.append("data: " + json.dumps(data) + "\n\n")
        chunks.append(
            "data: "
            + json.dumps(
                {
                    "id": "test",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "routed-answer-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
            )
            + "\n\n"
        )
        chunks.append(
            "data: "
            + json.dumps(
                {
                    "id": "test",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "routed-answer-model",
                    "choices": [],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
                }
            )
            + "\n\n"
        )
        chunks.append("data: [DONE]\n\n")
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, text="".join(chunks)
        )

    model = PolicyModel(Settings("test", "test", "123456"))
    model.client.close()
    model.client = OpenAI(
        api_key="test",
        base_url="https://example.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assessment = model.respond(
        "Who is the lead?",
        [],
        date(2026, 11, 16),
        Document(id="real-profile", title="Profile", text="# Clinical leads\nLead: Test person"),
        [],
        [],
        False,
        on_text=snapshots.append,
    )
    assert model.response_model == "routed-answer-model"
    assert model.calls == [("routed-answer-model", 150)]
    assert payloads[0]["stream"] is True
    assert payloads[0]["stream_options"] == {"include_usage": True}
    assert "The recorded " in snapshots
    assert snapshots[-1] == "The recorded lead is Test person."
    assert assessment["citations"][0]["policy_id"] == "real-profile"
    assert assessment["classification"] is None
    assert all("policy_id" not in text for text in snapshots)
    model.close()


def test_selection_inventory_contains_only_required_metadata():
    model, calls = compatible_model([json.dumps({"operation": "answer", "policy_ids": ["P1"]})])
    policy = Policy(
        id="p",
        title="Safety",
        tags=["Clinical"],
        owners=["Lead"],
        review_date="2027-01-01",
        version=42,
        public_url="https://example.test",
        last_edited_time="today",
    )
    model.select("Question", [], [policy])
    inventory = json.loads(calls[0]["messages"][1]["content"])["inventory"]
    assert inventory == [
        {
            "id": "P1",
            "title": "Safety",
            "tags": ["Clinical"],
            "owners": ["Lead"],
            "review_date": "2027-01-01",
        }
    ]
    model.close()


def test_gap_response_requires_synopsis_for_agent_advice():
    from datetime import date

    import pytest

    from nathan.domain import Document

    model = PolicyModel(Settings("test", "test", "123456"))

    def json_stage(instructions, payload, schema, **kwargs):
        return kwargs["validate"](
            {
                "answer": "Missing ECG coverage.",
                "citations": [],
                "classification": "Potential gap",
                "placeholder_title": "ECG Policy",
            }
        )

    model._json = json_stage
    try:
        with pytest.raises(ValueError, match="placeholder_advice"):
            model.respond(
                "ECG policy",
                [],
                date(2026, 11, 17),
                Document("profile", "Profile", "Text"),
                [],
                [],
                False,
            )
    finally:
        model.close()


def test_selector_receives_previous_topic_with_current_inventory_aliases():
    model, calls = compatible_model(['{"operation":"answer","policy_ids":["P2"]}'])
    history = [
        {"role": "user", "content": "What is our cold chain procedure?"},
        {
            "role": "assistant",
            "content": "Follow the cold chain policy.",
            "tools": ["Read Policy · Cold chain"],
            "model": "previous-model",
            "conversation_context": {
                "question": "What is our cold chain procedure?",
                "policies": [
                    {"id": "cold", "title": "Cold chain"},
                    {"id": "deleted", "title": "Deleted policy"},
                ],
            },
        },
    ]
    try:
        model.select(
            "Who is responsible for that?",
            history,
            [Policy("other", "Complaints"), Policy("cold", "Cold chain")],
        )
        payload = json.loads(calls[0]["messages"][1]["content"])
        assert payload["conversation_context"] == {
            "question": "What is our cold chain procedure?",
            "policies": [{"id": "P2", "title": "Cold chain"}],
        }
        assert payload["history"] == [
            {"role": "user", "content": "What is our cold chain procedure?"},
            {"role": "assistant", "content": "Follow the cold chain policy."},
        ]
        assert payload["ranked_metadata_matches"] == ["P2"]
    finally:
        model.close()


def _long_cold_chain_policy():
    parts = ["# Purpose\n\nThis policy sets out responsibilities.\n"]
    parts.append("# Fridge temperatures\n\nRecord the cold chain minimum and maximum every day.\n")
    for index in range(40):
        parts.append(f"# Unrelated {index}\n\n" + ("administrative wording " * 40))
    return "\n".join(parts)


def test_long_policy_sent_to_the_model_keeps_matching_sections():
    captured = {}
    model = PolicyModel(Settings("test", "test", "123456"))

    def json_stage(instructions, payload, schema, **kwargs):
        captured["payload"] = payload
        return kwargs["validate"](
            {
                "answer": "Record the fridge temperatures.",
                "citations": [{"policy_id": "P1", "section": "Fridge temperatures"}],
                "classification": None,
                "placeholder_title": None,
            }
        )

    model._json = json_stage
    try:
        model.respond(
            "What is the cold chain fridge temperature procedure?",
            [],
            date(2026, 11, 16),
            Document("profile", "Profile", "# Clinical leads\nLead: Test person"),
            [Policy("p", "Cold chain")],
            [Document("p", "Cold chain", _long_cold_chain_policy())],
            False,
        )
        document = captured["payload"]["documents"][0]
        assert document["text_scope"] == "relevant_sections"
        assert "Fridge temperatures" in document["text"]
        assert document["omitted_sections"]
        assert model.excerpted_titles == ["Cold chain"]
        assert captured["payload"]["profile"]["sections"] == ["Clinical leads"]
    finally:
        model.close()


def test_response_rejects_a_section_that_is_not_in_the_policy():
    model = PolicyModel(Settings("test", "test", "123456"))

    def json_stage(instructions, payload, schema, **kwargs):
        return kwargs["validate"](
            {
                "answer": "Call the lead.",
                "citations": [{"policy_id": "P1", "section": "Imaginary section"}],
                "classification": None,
                "placeholder_title": None,
            }
        )

    model._json = json_stage
    try:
        with pytest.raises(ValueError, match="not found"):
            model.respond(
                "Who is the lead?",
                [],
                date(2026, 11, 16),
                Document("profile", "Profile", "# Clinical leads\nLead: Test person"),
                [Policy("p", "Safety")],
                [Document("p", "Safety", "# Duties\nCall the lead.")],
                False,
            )
    finally:
        model.close()
