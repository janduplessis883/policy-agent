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
