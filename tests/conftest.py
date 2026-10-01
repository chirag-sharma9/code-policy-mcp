import pytest

from repo_policy.models import RepoRef
from repo_policy.providers.base import RepositoryProvider


class FakeProvider(RepositoryProvider):
    """In-memory repository: ``files`` maps path to content."""

    def __init__(self, files: dict[str, str], truncated: bool = False) -> None:
        self.files = files
        self.truncated = truncated
        self.calls = 0

    async def resolve(self, url: str) -> RepoRef:
        self.calls += 1
        return RepoRef(owner="acme", name="demo", default_branch="main", sha="abc123")

    async def list_files(self, ref: RepoRef) -> list[str]:
        self.calls += 1
        return list(self.files)

    async def tree_truncated(self, ref: RepoRef) -> bool:
        return self.truncated

    async def read_file(self, ref: RepoRef, path: str) -> str | None:
        self.calls += 1
        return self.files.get(path)


@pytest.fixture
def ref() -> RepoRef:
    return RepoRef(owner="acme", name="demo", default_branch="main", sha="abc123")
