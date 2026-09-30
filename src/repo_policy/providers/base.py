from typing import Protocol, runtime_checkable

from repo_policy.models import RepoRef


class ProviderError(Exception):
    """Base for failures the engine reports in ``RepoReport.error`` instead of raising."""


class InvalidUrlError(ProviderError):
    pass


class NotFoundError(ProviderError):
    pass


class RateLimitedError(ProviderError):
    pass


@runtime_checkable
class RepositoryProvider(Protocol):
    async def resolve(self, url: str) -> RepoRef: ...

    async def list_files(self, ref: RepoRef) -> list[str]: ...

    async def tree_truncated(self, ref: RepoRef) -> bool:
        """True when ``list_files`` is incomplete because the platform capped the listing."""
        ...

    async def read_file(self, ref: RepoRef, path: str) -> str | None: ...
