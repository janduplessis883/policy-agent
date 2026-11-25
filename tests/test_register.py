from datetime import date

from nathan.domain import Policy
from nathan.register import LIST_LIMIT, is_register_question, policy_status, render_register


def link(title, url):
    return f"[{title}]({url})" if url else title


def test_register_questions_are_about_the_inventory_not_a_procedure():
    assert is_register_question("Which policies are overdue?")
    assert is_register_question("What is due for review?")
    assert is_register_question("How many policies do we have?")
    assert is_register_question("List our policies")
    assert is_register_question("Show draft policies")
    assert not is_register_question("What is our cold chain procedure?")
    assert not is_register_question("Which policies cover cold chain?")
    assert not is_register_question("List policies for ECG")
    assert not is_register_question("Review our complaints policy")
    assert not is_register_question("Who is responsible for safeguarding?")
    assert not is_register_question("What should I do if a policy is overdue?")


def test_status_uses_the_same_live_rule_as_the_policy_record():
    today = date(2026, 11, 25)
    assert policy_status(Policy("a", "Current", review_date="2026-11-26"), today) == "Live"
    assert policy_status(Policy("b", "Today", review_date="2026-11-25"), today) == "Overdue"
    assert policy_status(Policy("c", "Old", review_date="2020-01-01"), today) == "Overdue"
    assert policy_status(Policy("d", "Undated"), today) == "No review date"
    assert (
        policy_status(Policy("e", "Broken", review_date="soon"), today) == "Review date unreadable"
    )
    assert (
        policy_status(
            Policy("f", "Held", review_date="2027-01-01", tags=["NEW POLICY REQUIRED"]), today
        )
        == "Placeholder"
    )
    assert (
        policy_status(Policy("g", "Drafted", review_date="2027-01-01", tags=["Draft"]), today)
        == "Draft"
    )


def test_overdue_register_lists_dates_and_hides_live_titles():
    today = date(2026, 11, 25)
    inventory = [
        Policy("live", "Infection control", review_date="2027-01-01", owners=["Lead"]),
        Policy(
            "old",
            "Cold chain",
            review_date="2024-03-01",
            owners=["Jan du Plessis"],
            public_url="https://example.test/cold",
        ),
        Policy("older", "Fire safety", review_date="2019-01-01"),
        Policy("none", "Complaints"),
        Policy("draft", "Chaperone", review_date="2027-06-01", tags=["Draft"]),
    ]
    text = render_register("Which policies are overdue?", inventory, today, link)
    assert "**5** policies. **1** Live. **4** not Live." in text
    assert text.index("Fire safety") < text.index("Cold chain")
    assert (
        "[Cold chain](https://example.test/cold) · Overdue · review date 2024-03-01 · Jan du Plessis"
        in text
    )
    assert "Complaints · No review date" in text
    assert "Chaperone · Draft" in text
    assert "Infection control" not in text
    assert "not listed here" in text
    assert "not a procedure" in text


def test_full_register_includes_live_titles_and_caps_each_group():
    today = date(2026, 11, 25)
    inventory = [Policy("live", "Current policy", review_date="2027-01-01")]
    inventory += [
        Policy(str(i), f"Policy {i:02d}", review_date="2020-01-01") for i in range(LIST_LIMIT + 1)
    ]
    text = render_register("Show the policy register", inventory, today, link)
    assert f"**{LIST_LIMIT + 2}** policies. **1** Live." in text
    assert "Current policy · Live · review date 2027-01-01" in text
    assert "Policy 00" in text
    assert f"Policy {LIST_LIMIT - 1:02d}" in text
    assert f"Policy {LIST_LIMIT:02d}" not in text
    assert "- And 1 more." in text


def test_empty_inventory_is_explicit():
    text = render_register("How many policies do we have?", [], date(2026, 11, 25), link)
    assert "**0** policies" in text
    assert "no policy records" in text


def _page(page_id, **properties):
    return {"id": page_id, "properties": properties}


def test_messy_notion_pages_do_not_break_the_register():
    from nathan.tools.list_policies import list_policies

    class Client:
        def query(self, filter=None):
            return [
                _page(
                    "nulls",
                    Tags=None,
                    Policy={"title": None},
                    Review_Date={"date": None},
                    Status={"type": "status", "status": {"name": None}},
                    Policy_Owner={"multi_select": [{"name": None}]},
                ),
                _page(
                    "formula",
                    Policy={"title": [{"plain_text": "Cold chain"}, {"text": None}]},
                    Tags={"multi_select": [{"name": None}, {"name": "Clinical"}]},
                    Review_Date={
                        "type": "formula",
                        "formula": {"type": "date", "date": {"start": "2020-01-01"}},
                    },
                    Policy_Owner={
                        "type": "people",
                        "people": [{"name": None}, {"name": "Jan du Plessis"}],
                    },
                ),
                _page(
                    "uk",
                    Policy={"type": "title", "title": [{"plain_text": "Complaints"}]},
                    Review_Date={
                        "type": "rich_text",
                        "rich_text": [{"plain_text": "15/03/2019"}],
                    },
                ),
                _page(
                    "rollup",
                    Policy={"title": [{"plain_text": "Fire"}]},
                    Review_Date={
                        "type": "rollup",
                        "rollup": {
                            "type": "array",
                            "array": [{"type": "date", "date": {"start": "2019-05-01"}}],
                        },
                    },
                ),
                _page(
                    "profile",
                    Tags={"multi_select": [{"name": "SURGERY PROFILE"}]},
                    Policy={"title": [{"plain_text": "Profile"}]},
                ),
                {"not": "a page"},
            ]

    policies = list_policies(Client())
    by_id = {policy.id: policy for policy in policies}
    assert "profile" not in by_id
    assert by_id["formula"].review_date == "2020-01-01"
    assert by_id["formula"].tags == ["Clinical"]
    assert by_id["formula"].owners == ["Jan du Plessis"]
    assert by_id["uk"].review_date == "15/03/2019"
    assert by_id["rollup"].review_date == "2019-05-01"
    assert any(policy.title == "Unreadable record" for policy in policies)
    text = render_register("Which policies are overdue?", policies, date(2026, 11, 25), link)
    assert "Cold chain · Overdue · review date 2020-01-01 · Jan du Plessis" in text
    assert "Complaints · Overdue · review date 2019-03-15" in text
    assert "Fire · Overdue · review date 2019-05-01" in text
    assert "Unreadable record" in text
