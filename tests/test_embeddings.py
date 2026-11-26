import json
import logging

import httpx

from nathan.embeddings import OPENROUTER_EMBEDDINGS_URL, Embedder
from nathan.index_sync import EMBEDDING_DIMENSIONS


class _Settings:
    def __init__(self, key="sk-or-test", model="openai/text-embedding-3-small"):
        self.openrouter_api_key = key
        self.embedding_model = model


def _vector(marker, index=0):
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = float(marker)
    values[1] = float(index)
    return values


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_embeddings_post_to_openrouter_with_attribution_headers():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["referer"] = request.headers["http-referer"]
        seen["title"] = request.headers["x-title"]
        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": _vector(1)}]},
        )

    embedder = Embedder(_Settings(), client=_client(handler), sleep=lambda _delay: None)
    vectors = embedder.embed(["fridge temperatures"])
    assert vectors == [_vector(1)]
    assert seen["url"] == OPENROUTER_EMBEDDINGS_URL
    assert seen["authorization"] == "Bearer sk-or-test"
    assert seen["referer"] == "https://github.com/janduplessis883/policy-agent"
    assert seen["title"] == "Nathan"
    assert seen["body"]["model"] == "openai/text-embedding-3-small"
    assert seen["body"]["dimensions"] == 1536
    assert seen["body"]["input"] == ["fridge temperatures"]


def test_embeddings_are_batched_and_returned_in_input_order():
    inputs = []

    def handler(request):
        body = json.loads(request.content)
        inputs.append(body["input"])
        batch = len(inputs)
        data = [
            {"index": index, "embedding": _vector(batch, index)}
            for index in range(len(body["input"]))
        ]
        data.reverse()
        return httpx.Response(200, json={"data": data})

    embedder = Embedder(
        _Settings(), client=_client(handler), sleep=lambda _delay: None, batch_size=2
    )
    vectors = embedder.embed(["one", "two", "three"])
    assert inputs == [["one", "two"], ["three"]]
    assert vectors[0][:2] == [1.0, 0.0]
    assert vectors[1][:2] == [1.0, 1.0]
    assert vectors[2][:2] == [2.0, 0.0]


def test_retry_on_429_then_success_and_key_is_redacted(caplog):
    calls = {"n": 0}
    slept = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "0"},
                json={"error": {"message": "slow down sk-or-test"}},
            )
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": _vector(1)}]})

    embedder = Embedder(_Settings(), client=_client(handler), sleep=slept.append)
    with caplog.at_level(logging.WARNING):
        vectors = embedder.embed(["hello"])
    assert vectors == [_vector(1)]
    assert calls["n"] == 2
    assert slept == [0.0]
    assert "HTTP 429" in caplog.text
    assert "sk-or-test" not in caplog.text


def test_retry_on_503_and_stop_on_400(caplog):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": _vector(1)}]})

    embedder = Embedder(_Settings(), client=_client(handler), sleep=lambda _delay: None)
    assert embedder.embed(["hello"]) == [_vector(1)]
    assert calls["n"] == 2

    calls["n"] = 0

    def rejected(request):
        calls["n"] += 1
        return httpx.Response(400, text="bad request sk-or-test")

    embedder = Embedder(_Settings(), client=_client(rejected), sleep=lambda _delay: None)
    with caplog.at_level(logging.WARNING):
        assert embedder.embed(["hello"]) == [None]
    assert calls["n"] == 1
    assert embedder.last_error.startswith("HTTP 400:")
    assert "bad request" in embedder.last_error
    assert "sk-or-test" not in embedder.last_error
    assert "sk-or-test" not in caplog.text


def test_wrong_dimension_is_rejected():
    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    embedder = Embedder(_Settings(), client=_client(handler), sleep=lambda _delay: None)
    assert embedder.embed(["hello"]) is None
    assert "1536" in embedder.last_error


def test_blank_inputs_are_not_sent():
    def handler(request):
        raise AssertionError("blank input must not be posted")

    embedder = Embedder(_Settings(), client=_client(handler), sleep=lambda _delay: None)
    assert embedder.embed(["", "   ", "\x00"]) == [None, None, None]


def test_oversized_input_is_split_before_the_request():
    from nathan.chunks import EMBED_TOKEN_LIMIT, estimate_tokens

    lengths = []

    def handler(request):
        body = json.loads(request.content)
        for item in body["input"]:
            lengths.append(len(item))
            assert estimate_tokens(item) <= EMBED_TOKEN_LIMIT
        data = [
            {"index": index, "embedding": _vector(1, index)} for index in range(len(body["input"]))
        ]
        return httpx.Response(200, json={"data": data})

    embedder = Embedder(_Settings(), client=_client(handler), sleep=lambda _delay: None)
    vectors = embedder.embed(["y" * 30_000])
    assert len(lengths) >= 2
    assert vectors[0] is not None
    assert len(vectors[0]) == EMBEDDING_DIMENSIONS


def test_a_400_on_a_batch_retries_each_chunk(caplog):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(list(body["input"]))
        if len(body["input"]) > 1:
            return httpx.Response(
                400, json={"error": {"code": "batch", "message": "bad batch sk-or-test"}}
            )
        if body["input"][0] == "bad":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "invalid_input",
                        "message": "empty string is not allowed",
                    }
                },
            )
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": _vector(1)}]})

    embedder = Embedder(_Settings(), client=_client(handler), sleep=lambda _delay: None)
    with caplog.at_level(logging.WARNING):
        vectors = embedder.embed(["bad", "good"])
    assert vectors[0] is None
    assert vectors[1] == _vector(1)
    assert seen[0] == ["bad", "good"]
    assert ["bad"] in seen
    assert ["good"] in seen
    assert "invalid_input" in embedder.last_error
    assert "empty string is not allowed" in embedder.last_error
    assert "sk-or-test" not in embedder.last_error
    assert "sk-or-test" not in caplog.text


def test_missing_openrouter_key_skips_the_request_and_logs(caplog):
    def handler(request):
        raise AssertionError("OpenRouter should not be called")

    with caplog.at_level(logging.WARNING):
        embedder = Embedder(_Settings(key=""), client=_client(handler))
        assert embedder.embed(["hello"]) is None
    assert embedder.last_error == "OPENROUTER_API_KEY is not set"
    assert "OPENROUTER_API_KEY is not set" in caplog.text
    assert "full-text search still runs" in caplog.text
    assert caplog.text.count("OPENROUTER_API_KEY is not set") == 1
