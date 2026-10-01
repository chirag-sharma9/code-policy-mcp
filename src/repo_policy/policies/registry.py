from repo_policy.policies.base import Policy


class PolicyRegistry:
    def __init__(self) -> None:
        self._policies: dict[str, Policy] = {}

    def register(self, policy: Policy) -> Policy:
        if policy.id in self._policies:
            raise ValueError(f"Policy id {policy.id!r} is already registered")
        self._policies[policy.id] = policy
        return policy

    def get(self, policy_id: str) -> Policy:
        try:
            return self._policies[policy_id]
        except KeyError:
            raise KeyError(f"Unknown policy {policy_id!r}; known: {sorted(self._policies)}") from None

    def all(self) -> list[Policy]:
        return list(self._policies.values())


registry = PolicyRegistry()
