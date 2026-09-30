from abc import ABC, abstractmethod

from repo_policy.models import PolicyResult, RepoRef
from repo_policy.providers.base import RepositoryProvider


class Policy(ABC):
    """One deterministic check against a repository at a fixed commit."""

    id: str
    description: str

    @abstractmethod
    async def evaluate(self, ref: RepoRef, provider: RepositoryProvider) -> PolicyResult: ...
