from repo_policy.models import RepoRef, RepoReport

CacheKey = tuple[str, str, str, str, tuple[str, ...]]


def cache_key(ref: RepoRef, policy_ids: list[str]) -> CacheKey:
    return (ref.provider, ref.owner, ref.name, ref.sha, tuple(sorted(policy_ids)))


class ReportCache:
    """In-memory report cache. A report at a given commit SHA never goes stale, so entries have no expiry."""

    def __init__(self) -> None:
        self._reports: dict[CacheKey, RepoReport] = {}

    def get(self, key: CacheKey) -> RepoReport | None:
        return self._reports.get(key)

    def set(self, key: CacheKey, report: RepoReport) -> None:
        self._reports[key] = report

    def __len__(self) -> int:
        return len(self._reports)
