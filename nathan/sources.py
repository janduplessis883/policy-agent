from nathan.tools.get_surgery_profile import get_surgery_profile
from nathan.tools.list_policies import list_policies
from nathan.tools.read_policy import read_policy


class Sources:
    def __init__(self, client):
        self.client = client

    def profile(self):
        return get_surgery_profile(self.client)

    def inventory(self):
        return list_policies(self.client)

    def read(self, policy):
        return read_policy(self.client, policy)
