from nathan.domain import Policy
from nathan.policy_search import lexical_policy_ids, ranked_policy_ids


def test_synonyms_match_titles_and_tags_and_rank_specific_policy_first():
    inventory = [
        Policy("unrelated", "Fire safety"),
        Policy("broad", "Diagnostic services"),
        Policy("tagged", "Recording procedure", tags=["ECG"]),
        Policy("exact", "Electrocardiography"),
    ]
    matches = ranked_policy_ids("Create a policy for ECGs", inventory)
    assert matches[0] in {"exact", "tagged"}
    assert set(matches) == {"broad", "tagged", "exact"}


def test_common_synonyms_and_generic_words_do_not_match_unrelated_policies():
    inventory = [Policy("v", "Vaccine storage"), Policy("f", "Fire Policy")]
    assert ranked_policy_ids("Review our cold chain policy", inventory) == ["v"]
    assert ranked_policy_ids("Create a policy please", inventory) == []


def test_plural_immunisation_query_finds_vaccination_title():
    assert ranked_policy_ids("Review immunisations", [Policy("v", "Vaccination")]) == ["v"]


def test_multi_word_topic_does_not_match_one_shared_word():
    inventory = [
        Policy("chain", "Chain of command"),
        Policy("cold", "Cold chain"),
        Policy("vaccine", "Vaccine fridge temperatures"),
    ]
    matches = ranked_policy_ids("What is our cold chain procedure?", inventory)
    assert matches[0] == "cold"
    assert "vaccine" in matches
    assert "chain" not in matches


def test_confidentiality_matches_information_governance_not_fire_safety():
    inventory = [Policy("ig", "Information governance"), Policy("fire", "Fire safety")]
    assert ranked_policy_ids("Where is our confidentiality procedure?", inventory) == ["ig"]


def test_followup_with_no_own_keywords_reuses_the_previous_topic():
    inventory = [Policy("cold", "Cold chain"), Policy("fire", "Fire safety")]
    assert lexical_policy_ids(
        "Who is responsible for that?",
        inventory,
        previous_question="What is our cold chain procedure?",
    ) == ["cold"]
    assert (
        lexical_policy_ids(
            "What is the weather today?",
            inventory,
            previous_question="What is our cold chain procedure?",
        )
        == []
    )
