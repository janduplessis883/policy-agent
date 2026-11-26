import logging

from nathan.domain import Policy, policy_from_page

logger = logging.getLogger(__name__)


def _is_profile(policy: Policy) -> bool:
    return any(isinstance(tag, str) and tag.strip() == "SURGERY PROFILE" for tag in policy.tags)


def list_policies(client):
    """Every readable record. One bad page is listed as unreadable and does not stop the rest."""
    policies = []
    for index, page in enumerate(client.query(), start=1):
        try:
            policy = policy_from_page(page)
        except Exception:
            page_id = page.get("id") if isinstance(page, dict) else None
            logger.exception("Could not read policy page %s (%s)", index, page_id)
            policies.append(Policy(id=f"unreadable-{index}", title="Unreadable record"))
            continue
        if _is_profile(policy):
            continue
        policies.append(policy)
    return policies
