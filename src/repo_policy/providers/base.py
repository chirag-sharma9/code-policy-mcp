from abc import ABC, abstractmethod

from repo_policy.models import RepoRef


class ProviderError(Exception):
    """Base for failures the engine reports in ``RepoReport.error`` instead of raising."""


class InvalidUrlError(ProviderError):
    pass


class NotFoundError(ProviderError):
    pass


class RateLimitedError(ProviderError):
    pass


class RepositoryProvider(ABC):
    """Read-only view of a repository at one commit. Policies depend on this and nothing else."""

    @abstractmethod
    async def resolve(self, url: str) -> RepoRef: ...

    @abstractmethod
    async def list_files(self, ref: RepoRef) -> list[str]: ...

    @abstractmethod
    async def tree_truncated(self, ref: RepoRef) -> bool:
        """True when ``list_files`` is incomplete because the platform capped the listing."""
        ...

    @abstractmethod
    async def read_file(self, ref: RepoRef, path: str) -> str | None: ...
