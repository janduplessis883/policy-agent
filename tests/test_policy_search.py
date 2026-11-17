from nathan.domain import Policy
from nathan.policy_search import ranked_policy_ids


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
