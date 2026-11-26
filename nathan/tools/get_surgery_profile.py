from nathan.domain import SourceError, policy_from_page
from nathan.tools.read_policy import read_policy


def get_surgery_profile(client):
    pages = client.query({"property": "Tags", "multi_select": {"contains": "SURGERY PROFILE"}})
    active = [
        page
        for page in pages
        if isinstance(page, dict) and not page.get("archived") and not page.get("in_trash")
    ]
    if len(active) != 1:
        raise SourceError(
            "Current staffing and responsibilities could not be verified: "
            "exactly one accessible Surgery Profile is required."
        )
    profile = read_policy(client, policy_from_page(active[0]))
    if not profile.complete or not profile.text.strip():
        raise SourceError(
            "The Surgery Profile could not be read in full. Current staffing "
            "and responsibility information could not be verified."
        )
    return profile
