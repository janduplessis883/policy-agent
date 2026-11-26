from datetime import date

import pytest

from nathan.agent import Agent
from nathan.domain import Policy, SourceError
from nathan.tools.list_overdue_policies import is_overdue_question, list_overdue_policies

SCOPE_MESSAGE = (
    "The requested policy scope is too large for one verified response. "
    "Please narrow the question or review scope."
)
PHRASES = [
    "list all overdue policies",
    "show out of date policies",
    "show out-of-date policies",
    "what policies are due for review",
    "which policies need reviewing",
    "which policies are expired",
    "which policies are past their review",
    "what are the review dates",
]


class Sources:
    def __init__(self, policies):
        self.calls = []
        self.policies = policies

    def profile(self):
        self.calls.append("profile")
        raise AssertionError("overdue questions must not read the Surgery Profile")

    def inventory(self):
        self.calls.append("inventory")
        return self.policies

    def read(self, policy):
        self.calls.append("read")
        raise AssertionError("overdue questions must not read policy text")


class ScopeModel:
    def select(self, *args):
        raise SourceError(SCOPE_MESSAGE)

    def respond(self, *args):
        raise SourceError(SCOPE_MESSAGE)


def test_overdue_phrases_are_detected_and_procedures_are_not():
    for phrase in PHRASES:
        assert is_overdue_question(phrase), phrase
    assert not is_overdue_question("What is our cold chain procedure?")
    assert not is_overdue_question("Which policies cover cold chain?")
    assert not is_overdue_question("Review our complaints policy")
    assert not is_overdue_question("What should I do if a policy is overdue?")


@pytest.mark.parametrize("question", PHRASES)
def test_phrasing_uses_the_overdue_tool_and_never_the_size_limit(question):
    policies = [
        Policy("old", "Cold chain", review_date="2020-01-01", owners=["Jan"], tags=["Clinical"]),
        Policy("soon", "Safeguarding", review_date="2026-12-10", tags=["Clinical"]),
        Policy("live", "Infection control", review_date="2027-06-01"),
        Policy("draft", "Chaperone", review_date="2020-01-01", tags=["Draft"]),
    ]
    sources = Sources(policies)
    result = Agent(sources, ScopeModel(), clock=lambda: date(2026, 11, 25)).ask(question, [])
    assert sources.calls == ["inventory"]
    assert SCOPE_MESSAGE not in result.text
    assert "Please narrow the question" not in result.text
    assert "Cold chain" in result.text
    assert "review date 2020-01-01" in result.text
    assert "Jan" in result.text
    assert "tags: Clinical" in result.text
    assert "Policy text was not read" in result.text
    assert result.source_note


def test_more_than_one_hundred_overdue_policies_are_all_listed():
    policies = [
        Policy(f"p{index}", f"Policy {index:03d}", review_date="2019-01-01") for index in range(101)
    ]
    text = list_overdue_policies(policies, date(2026, 11, 25), "list all overdue policies")
    assert "**101** overdue" in text
    assert "Policy 000" in text
    assert "Policy 100" in text
    assert "And " not in text
    assert "15 policies" not in text.split("not limited to 15 policies")[-1]


def test_paginated_inventory_feeds_the_overdue_tool():
    import json

    import httpx

    from nathan.notion import NotionClient
    from nathan.tools.list_policies import list_policies

    pages = []
    for index in range(101):
        pages.append(
            {
                "id": f"p{index}",
                "properties": {
                    "Policy": {"title": [{"plain_text": f"Policy {index:03d}"}]},
                    "Tags": {"multi_select": None} if index == 7 else {"multi_select": []},
                    "Review_Date": {"date": {"start": "2018-04-01"}},
                    "Policy_Owner": {"multi_select": [{"name": None}, {"name": "Owner"}]},
                },
            }
        )

    def handler(request):
        body = json.loads(request.content or b"{}")
        batch = pages[100:] if body.get("start_cursor") == "next" else pages[:100]
        more = body.get("start_cursor") != "next"
        return httpx.Response(
            200,
            json={
                "results": batch,
                "has_more": more,
                "next_cursor": "next" if more else None,
            },
        )

    client = NotionClient("ntn_shouldnotleak", httpx.MockTransport(handler))
    policies = list_policies(client)
    text = list_overdue_policies(policies, date(2026, 11, 25), "list all overdue policies")
    assert len(policies) == 101
    assert "**101** overdue" in text
    assert "Policy 100" in text
    assert "Owner" in text
    client.close()


def test_due_soon_window_is_separate_from_overdue():
    policies = [
        Policy("old", "Cold chain", review_date="2020-01-01"),
        Policy("soon", "Safeguarding", review_date="2026-12-01"),
        Policy("later", "Fire safety", review_date="2027-06-01"),
        Policy("none", "Complaints"),
    ]
    text = list_overdue_policies(policies, date(2026, 11, 25), "which policies are due soon?")
    due = text.split("### Due within 30 days", 1)[1].split("###", 1)[0]
    overdue = text.split("### Overdue", 1)[1]
    assert "Safeguarding" in due
    assert "review date 2026-12-01" in due
    assert "Cold chain" not in due
    assert "Fire safety" not in text
    assert "Cold chain" in overdue
    assert "Complaints" in text
