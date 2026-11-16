import httpx
import pytest

from nathan.domain import Policy, SourceError, UncertainWrite
from nathan.notion import NotionClient
from nathan.tools.get_surgery_profile import get_surgery_profile
from nathan.tools.read_policy import read_policy


def test_paginated_inventory():
    def handler(request):
        import json

        data = json.loads(request.content)
        if data.get("start_cursor") == "next":
            return httpx.Response(200, json={"results": [{"id": "b"}], "has_more": False})
        return httpx.Response(
            200, json={"results": [{"id": "a"}], "has_more": True, "next_cursor": "next"}
        )

    client = NotionClient("secret", httpx.MockTransport(handler))
    assert [p["id"] for p in client.query()] == ["a", "b"]


def test_write_timeout_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout")

    client = NotionClient("secret", httpx.MockTransport(handler))
    with pytest.raises(UncertainWrite):
        client.request("POST", "pages", write=True, json={})
    assert len(calls) == 1


def test_markdown_endpoint_version_and_parameters():
    def handler(request):
        assert request.url.path == "/v1/pages/p/markdown"
        assert request.url.params["markdown_version"] == "v2"
        assert request.headers["Notion-Version"] == "2026-03-11"
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "object": "page_markdown",
                "id": "p",
                "markdown": "# Safety\n\nCall the lead.",
                "truncated": False,
                "unknown_block_ids": [],
            },
        )

    client = NotionClient("secret", httpx.MockTransport(handler))
    doc = read_policy(client, Policy(id="p", title="Safety"))
    assert doc.text == "# Safety\n\nCall the lead."
    assert doc.complete


def test_markdown_recovers_truncated_subtree_in_place():
    class Client:
        def markdown(self, block):
            if block == "p":
                return {
                    "markdown": '# Procedure\n<unknown url="https://notion.so/#child" />\nEnd',
                    "truncated": True,
                    "unknown_block_ids": ["child"],
                }
            return {
                "markdown": "Recovered instructions",
                "truncated": False,
                "unknown_block_ids": [],
            }

    doc = read_policy(Client(), Policy(id="p", title="Safety"))
    assert doc.text == "# Procedure\nRecovered instructions\nEnd"
    assert doc.complete


@pytest.mark.parametrize(
    "payload",
    [
        {
            "markdown": '<unknown url="https://notion.so/#blocked" />',
            "truncated": True,
            "unknown_block_ids": ["blocked"],
        },
        {"markdown": "Partial", "truncated": True, "unknown_block_ids": []},
        {"markdown": '<unknown alt="unsupported" />', "truncated": False, "unknown_block_ids": []},
        {
            "markdown": '<pdf source="https://example.test/file.pdf" />',
            "truncated": False,
            "unknown_block_ids": [],
        },
    ],
)
def test_markdown_missing_content_is_not_complete(payload):
    class Client:
        def markdown(self, block):
            if block != "p":
                raise SourceError("Not accessible")
            return payload

    assert not read_policy(Client(), Policy(id="p", title="Safety")).complete


def test_multiple_profiles_fail_closed():
    class Client:
        def query(self, filter):
            return [{"id": "a"}, {"id": "b"}]

    with pytest.raises(SourceError):
        get_surgery_profile(Client())


def test_markdown_subtree_cycles_stop_safely():
    class Client:
        def markdown(self, block):
            return {
                "markdown": '<unknown url="https://notion.so/#p" />',
                "truncated": True,
                "unknown_block_ids": ["p"],
            }

    assert not read_policy(Client(), Policy(id="p", title="Safety")).complete


def test_profile_uses_markdown_reader():
    class Client:
        def query(self, filter):
            return [
                {"id": "profile", "properties": {"Policy": {"title": [{"plain_text": "Profile"}]}}}
            ]

        def markdown(self, block):
            assert block == "profile"
            return {
                "markdown": "# Clinical leads\nRecorded lead: Test person",
                "truncated": False,
                "unknown_block_ids": [],
            }

    assert "Clinical leads" in get_surgery_profile(Client()).text
