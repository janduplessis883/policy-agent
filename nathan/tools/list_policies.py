from nathan.domain import policy_from_page


def list_policies(client):
    return [
        policy_from_page(p)
        for p in client.query()
        if "SURGERY PROFILE"
        not in [t["name"] for t in p.get("properties", {}).get("Tags", {}).get("multi_select", [])]
    ]
