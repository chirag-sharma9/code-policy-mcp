import asyncio
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

import httpx

from repo_policy.cache import ReportCache, cache_key
from repo_policy.models import PolicyResult, RepoReport
from repo_policy.policies import PolicyRegistry
from repo_policy.policies import registry as default_registry
from repo_policy.policies.base import Policy
from repo_policy.providers.base import InvalidUrlError, ProviderError, RepositoryProvider
from repo_policy.providers.github import GitHubProvider

ProviderFactory = Callable[[], RepositoryProvider]


class Engine:
    """Turns a repository URL into a RepoReport. Never raises for user-facing failures."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        registry: PolicyRegistry = default_registry,
        cache: ReportCache | None = None,
        providers: Mapping[str, ProviderFactory] | None = None,
    ) -> None:
        self._registry = registry
        self._cache = cache if cache is not None else ReportCache()
        if providers is None:
            if client is None:
                raise ValueError("Engine needs an httpx.AsyncClient or an explicit providers mapping")
            providers = {"github.com": lambda: GitHubProvider(client)}
        self._providers = dict(providers)

    async def check(self, url: str, policy_ids: list[str] | None = None) -> RepoReport:
        try:
            policies = self._select_policies(policy_ids)
            provider = self._provider_for(url)
            ref = await provider.resolve(url)
        except (KeyError, ProviderError) as exc:
            return RepoReport(url=url, error=_message(exc))

        key = cache_key(ref, [p.id for p in policies])
        if (cached := self._cache.get(key)) is not None:
            by_id = {r.policy_id: r for r in cached.results}
            return cached.model_copy(update={"url": url, "results": [by_id[p.id] for p in policies]})

        outcomes = await asyncio.gather(*(p.evaluate(ref, provider) for p in policies), return_exceptions=True)
        results: list[PolicyResult] = []
        failures: list[str] = []
        for policy, outcome in zip(policies, outcomes):
            if isinstance(outcome, BaseException):
                failures.append(f"{policy.id}: {_message(outcome)}")
            else:
                results.append(outcome)

        report = RepoReport(url=url, ref=ref, results=results, error="; ".join(failures) or None)
        if report.error is None:
            self._cache.set(key, report)
        return report

    def _select_policies(self, policy_ids: list[str] | None) -> list[Policy]:
        if policy_ids is None:
            return self._registry.all()
        if not policy_ids:
            raise KeyError("policies must be omitted (run all) or a non-empty list of policy ids")
        return [self._registry.get(pid) for pid in dict.fromkeys(policy_ids)]

    def _provider_for(self, url: str) -> RepositoryProvider:
        raw = url.strip()
        host = (urlsplit(raw if "://" in raw else "https://" + raw).hostname or "").lower().removeprefix("www.")
        try:
            return self._providers[host]()
        except KeyError:
            supported = ", ".join(sorted(self._providers))
            raise InvalidUrlError(f"Unsupported repository host {host or '<none>'!r}; supported: {supported}") from None


def _message(exc: BaseException) -> str:
    text = str(exc).strip("'\"") if isinstance(exc, KeyError) else str(exc)
    return text if isinstance(exc, (KeyError, ProviderError)) else f"{type(exc).__name__}: {text}"
