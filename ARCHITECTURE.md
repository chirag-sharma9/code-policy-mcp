# Architecture Specification - Repository Policy Checker (MCP)

## 1. Problem

Trustible validates that governance policies are being followed. This project scopes that to
three checks against a public GitHub repository, exposed to AI agents over MCP:

1. Automated tests are set up (CI configuration present).
2. A README exists and contains a contact email.
3. The `scikit-learn` package is used (declared as a dependency or imported in code).

The interesting part is not the three checks. It is building them so that the fourth check,
the second platform, the hundredth repository, and the customer-defined rule are cheap - **scalability**.

## 2. Design goals

- **Deterministic.** A governance verdict must be reproducible and explainable. No LLM in the
  evaluation path. The agent asks; the server computes; the agent reports.
- **Evidence, not booleans.** Every result carries what was found and where, so a human can
  audit it.
- **Small surface, clear seams.** Three abstractions - provider, policy, result. Nothing
  speculative built on top of them.
- **Cheap per repository.** No cloning. A handful of HTTP calls per repo, cost should scale with the number of relevant files, not repo size.

## 3. High-level shape

```
AI agent ──▶ MCP (stdio) ──▶ server.py ──▶ Engine ──▶ [Policy, Policy, Policy]
                                            │              │
                                            │              ▼
                                            └──▶ RepositoryProvider (GitHub REST via httpx)
                                                         │
                                                         ▼
                                                  Cache (keyed on commit SHA)
```

- `server.py` - FastMCP server. Two tools: `check_repository`, `list_policies`.
- `engine.py` - parses the URL, resolves the provider, resolves the commit SHA, runs the
  requested policies concurrently, assembles the report.
- `providers/` - `RepositoryProvider` interface; `GitHubProvider` implementation.
- `policies/` - `Policy` interface, three implementations, a registry.
- `models.py` - Pydantic models for results and the report.
- `cache.py` - in-memory dictionary keyed on `(owner, repo, sha)`.

## 4. Key decisions and trade-offs

### 4.1 GitHub REST API instead of cloning

**Decision.** Fetch the default branch, one recursive `git/trees` call for the file listing,
then fetch only the specific files a policy needs (`contents` endpoint, raw media type).

**Why.** Cloning is O(repo size) in time and disk; the API is O(files we care about). For
hundreds of repos this is the difference between minutes and seconds, and no local state.

**Cost.** The recursive tree endpoint truncates above ~100k entries; we detect the `truncated`
flag and report reduced confidence rather than failing. Unauthenticated calls are limited to
60/hour; an optional `GITHUB_TOKEN` raises this to 5,000/hour and enables private repos.

**Considered and rejected.** Consuming GitHub's official MCP server as the data source. It adds
a process hop and an auth dependency to do what one `GET` does, and it is oriented toward
agent-driven read/write workflows rather than deterministic batch evaluation. The
`RepositoryProvider` interface would allow a `GitHubMCPProvider` later if a shared auth story
mattered.

### 4.2 `RepositoryProvider` interface

```python
class RepositoryProvider(Protocol):
    async def resolve(self, url: str) -> RepoRef                      # owner, name, default branch, sha
    async def list_files(self, ref: RepoRef) -> list[str]             # paths at that sha
    async def read_file(self, ref: RepoRef, path: str) -> str | None
```

Policies only ever see this interface. Adding Bitbucket or GitLab is a new class; policies do
not change. URL parsing decides which provider handles a request.

### 4.3 `Policy` interface and registry

```python
class Policy(Protocol):
    id: str
    description: str
    async def evaluate(self, ref: RepoRef, provider: RepositoryProvider) -> PolicyResult
```

Policies are registered by id. `list_policies` exposes them to agents so an agent can discover
what it can ask for; `check_repository` accepts an optional subset.

`PackageUsagePolicy` is parameterized (`package`, `import_names`, `manifests`). The
`scikit-learn` check is one configured instance. This is the seam for user-defined rules
(see §5.4).

### 4.4 Results carry evidence and confidence

```python
class Evidence(BaseModel):
    path: str
    line: int | None = None
    snippet: str | None = None

class PolicyResult(BaseModel):
    policy_id: str
    passed: bool
    confidence: Literal["high", "medium", "low"]
    evidence: list[Evidence] = []
    notes: str | None = None
```

`confidence` exists because these checks are heuristics. A README email might be a `noreply`
address; a `tests/` directory without CI config is weaker evidence than a workflow file. The
policy says what it saw; a human decides what it means.

### 4.5 Async, concurrent policy evaluation

`httpx.AsyncClient` throughout; `asyncio.gather` across policies. Each policy fetches only what it
needs; shared reads (README, file list) are memoized per request so three policies do not
trigger three tree calls.

### 4.6 Caching on commit SHA

A repository at a given SHA is immutable, so a report at that SHA is valid forever. Cache key
is `(provider, owner, repo, sha, policy_ids)`. In-memory for this exercise; Redis with no
expiry in production.

### 4.7 MCP transport: stdio

Stdio drops into Claude Desktop and Claude Code with no network setup. We can switch to Streamable HTTP 
in FastMCP and it is the production choice (multiple clients, OAuth, load
balancing). Auth is out of scope for now (read-only tool over public repos). It is discussed in §5.5.

### 4.8 Policy heuristics

| Policy | Passes when | Evidence | Known gaps |
|---|---|---|---|
| `ci_tests` | Any of `.github/workflows/*.yml|yaml`, `.circleci/config.yml`, `.gitlab-ci.yml`, `Jenkinsfile`, `.travis.yml`, `azure-pipelines.yml` exists | Matching paths | A workflow that only lints still passes. A `tests/` dir without CI is reported as `passed=false, confidence=low` with a note. |
| `contact_email` | A README (`README`, `README.md`, `README.rst`, any case) contains an RFC-ish email | Path, line, the address | Excludes `noreply`/`no-reply` and addresses inside image/badge URLs. Does not verify deliverability. |
| `package_usage[scikit-learn]` | `scikit-learn`/`sklearn` in `requirements*.txt`, `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile`, `environment.yml`; or `import sklearn` / `from sklearn` in any `.py` | Path, line, snippet | Import scan capped at 200 `.py` files (largest-first is not attempted; first 200 in tree order). Over the cap → `confidence=medium`. Comments and strings can false-positive. |

## 5. Scaling scenarios

### 5.1 More validations
New `Policy` class, register it, done. Nothing else changes. The report shape already handles N
results.

### 5.2 Other platforms (Bitbucket, GitLab, Azure DevOps)
New `RepositoryProvider`. The engine dispatches on URL host. Policies are untouched because they
only depend on `list_files` and `read_file`. The one leak to watch: CI config file names differ
by platform (`bitbucket-pipelines.yml`), so `ci_tests` grows its pattern list rather than
becoming provider-specific.

### 5.3 Hundreds of repositories
- Current design is already async and cheap per repo (~3–6 HTTP calls).
- Add a `check_repositories(urls)` tool that fans out with a bounded semaphore (respecting
  provider rate limits) and returns a summary plus per-repo reports.
- Move the cache to Redis keyed on SHA; most re-scans are cache hits because most repos do
  not change between scans.
- For thousands: a job queue (Celery/SQS) with the MCP tool submitting and polling, plus
  webhook-triggered re-evaluation on push so scans happen on change rather than on schedule.
- Rate limits are the real ceiling. A GitHub App installation token per organization scales
  limits with customers rather than sharing one PAT.

### 5.4 User-defined policies
Two tiers:
1. **Parameterized built-ins** (exists now): `PackageUsagePolicy(package="X")`,
   `FilePresencePolicy(pattern=...)`, `ContentMatchPolicy(path_glob=..., regex=...)`. Most
   customer asks ("scan for package X", "require a `SECURITY.md`") are instances of these.
2. **Declarative rules**: a JSON/YAML rule referencing a built-in by id with parameters, stored
   per tenant, loaded into the registry at startup. Validated with Pydantic so a bad rule fails
   at load, not at scan.

Rules that need data outside the repo ("only group Y can access") are a different provider -
a `PlatformAdminProvider` with `list_collaborators` - not a different policy model. The policy
interface stays the same; the provider grows.

### 5.5 Governance of the tool itself
In production the server would run over Streamable HTTP behind OAuth, with per-tool scopes
(`policies:read`, `repos:scan`) and an audit log of every tool call with caller identity. Evidence
in results is what makes the audit log useful.

## 6. What I would do next, in order

1. `check_repositories` batch tool with bounded concurrency.
2. Redis cache.
3. `ContentMatchPolicy` and `FilePresencePolicy` generics, then the declarative rule loader.
4. Bitbucket provider to prove the seam.
5. Evals: a fixed set of known repos with expected results, run in CI, so heuristic changes are
   measured rather than eyeballed.

## 7. Out of scope, deliberately

Web framework (Django/FastAPI) - nothing here needs HTTP routing beyond what FastMCP provides.
Database - results are derived and cacheable; persistence belongs with the batch/queue layer.
LLM-assisted checks - tempting for "does the README explain how to report issues", but they
belong in a separate policy class with `confidence=low` and are not needed for these three.
