import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Hashable, TypeVar
from urllib.parse import quote, urlsplit

import httpx

from repo_policy.models import RepoRef
from repo_policy.providers.base import (
    InvalidUrlError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
)

API_URL = "https://api.github.com"
GITHUB_HOSTS = {"github.com", "www.github.com"}
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")

T = TypeVar("T")


def parse_github_url(url: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` from a github.com URL, tolerating ``.git``, trailing slashes and deeper paths."""
    raw = url.strip()
    if not raw:
        raise InvalidUrlError("Repository URL is empty")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.hostname not in GITHUB_HOSTS:
        raise InvalidUrlError(f"Not a github.com URL: {url}")
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        raise InvalidUrlError(f"URL must look like https://github.com/<owner>/<repo>: {url}")
    owner, repo = segments[0], segments[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not (_NAME.match(owner) and _NAME.match(repo)):
        raise InvalidUrlError(f"Invalid owner or repository name in URL: {url}")
    return owner, repo


@dataclass(frozen=True)
class _Tree:
    paths: list[str]
    truncated: bool


class GitHubProvider:
    """GitHub REST implementation. Create one per ``check()`` call so memoization is request-scoped."""

    def __init__(self, client: httpx.AsyncClient, token: str | None = None) -> None:
        self._client = client
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN") or None
        self._memo: dict[Hashable, asyncio.Task[Any]] = {}

    async def resolve(self, url: str) -> RepoRef:
        owner, repo = parse_github_url(url)
        meta = (await self._get(f"/repos/{owner}/{repo}")).json()
        branch: str = meta["default_branch"]
        try:
            head = (await self._get(f"/repos/{owner}/{repo}/git/ref/heads/{quote(branch, safe='')}")).json()
        except NotFoundError:
            raise NotFoundError(f"Branch {branch!r} of {owner}/{repo} has no commits") from None
        return RepoRef(owner=owner, name=meta["name"], default_branch=branch, sha=head["object"]["sha"])

    async def list_files(self, ref: RepoRef) -> list[str]:
        return (await self._tree(ref)).paths

    async def tree_truncated(self, ref: RepoRef) -> bool:
        return (await self._tree(ref)).truncated

    async def read_file(self, ref: RepoRef, path: str) -> str | None:
        return await self._memoized(("file", ref, path), lambda: self._fetch_file(ref, path))

    async def _tree(self, ref: RepoRef) -> _Tree:
        return await self._memoized(("tree", ref), lambda: self._fetch_tree(ref))

    async def _fetch_tree(self, ref: RepoRef) -> _Tree:
        data = (
            await self._get(f"/repos/{ref.full_name}/git/trees/{ref.sha}", params={"recursive": "1"})
        ).json()
        paths = [entry["path"] for entry in data.get("tree", []) if entry.get("type") == "blob"]
        return _Tree(paths=paths, truncated=bool(data.get("truncated", False)))

    async def _fetch_file(self, ref: RepoRef, path: str) -> str | None:
        try:
            response = await self._get(
                f"/repos/{ref.full_name}/contents/{quote(path, safe='/')}",
                params={"ref": ref.sha},
                accept="application/vnd.github.raw+json",
            )
        except NotFoundError:
            return None
        return response.text

    async def _memoized(self, key: Hashable, factory: Callable[[], Awaitable[T]]) -> T:
        # Store the in-flight task, not the result, so concurrent policies share one fetch.
        task = self._memo.get(key)
        if task is None:
            task = asyncio.ensure_future(factory())
            self._memo[key] = task
        return await task

    async def _get(
        self, path: str, *, params: dict[str, str] | None = None, accept: str = "application/vnd.github+json"
    ) -> httpx.Response:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "repo-policy-mcp",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = await self._client.get(API_URL + path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"GitHub request failed: {exc}") from exc
        self._raise_for_status(response, path)
        return response

    def _raise_for_status(self, response: httpx.Response, path: str) -> None:
        status = response.status_code
        if status < 400:
            return
        remaining = response.headers.get("x-ratelimit-remaining")
        if status == 429 or (status == 403 and (remaining == "0" or "rate limit" in response.text.lower())):
            reset = response.headers.get("x-ratelimit-reset")
            hint = "set GITHUB_TOKEN to raise the limit" if not self._token else "wait for the window to reset"
            when = f"; resets at unix time {reset}" if reset else ""
            raise RateLimitedError(f"GitHub API rate limit exceeded{when}; {hint}")
        if status == 404:
            raise NotFoundError(f"GitHub returned 404 for {path} (missing, or private without a token)")
        if status in (401, 403):
            raise ProviderError(f"GitHub denied access to {path} (HTTP {status}); check GITHUB_TOKEN")
        raise ProviderError(f"GitHub returned HTTP {status} for {path}")
