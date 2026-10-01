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
  evaluation path. The agent asks -> the server computes -> the agent reports.
- **Evidence, not booleans.** Every result carries what was found and where, so a human can
  audit it.
- **Small surface, clear seperations.** Three abstractions - provider, policy, result. Nothing
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
class RepositoryProvider(ABC):
    @abstractmethod
    async def resolve(self, url: str) -> RepoRef                      # owner, name, default branch, sha
    @abstractmethod
    async def list_files(self, ref: RepoRef) -> list[str]             # paths at that sha
    @abstractmethod
    async def tree_truncated(self, ref: RepoRef) -> bool              # listing capped by the platform?
    @abstractmethod
    async def read_file(self, ref: RepoRef, path: str) -> str | None
```

Policies only ever see this interface. Adding Bitbucket or GitLab is a new subclass; policies do
not change. URL parsing decides which provider handles a request. Interfaces are abstract base
classes rather than `typing.Protocol`: a provider or policy that forgets a method fails when it
is instantiated, not when a policy first calls it mid-scan.

### 4.3 `Policy` interface and registry

```python
class Policy(ABC):
    id: str
    description: str
    @abstractmethod
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

`confidence` exists because these checks are heuristics. It answers one question: can the reader
act on `passed` without looking further? Every policy follows the same three rules:

| confidence | when | example |
|---|---|---|
| `high` | The verdict rests on concrete evidence, or on a complete search that found nothing. Every pass is `high`. | A workflow file exists; no README anywhere in a fully listed tree. |
| `medium` | The search was incomplete, so a fail might be wrong. Only fails can be `medium`. | The file listing was truncated; the import scan hit its 200-file cap. |
| `low` | Something related was found that the rule cannot count. A human should look. Only fails can be `low`. | `tests/` exists but no CI config; the README's only addresses are `noreply` or inside URLs. |

The policy says what it saw, a human decides what it means.

### 4.5 Async, concurrent policy evaluation

`httpx.AsyncClient` throughout, `asyncio.gather` across policies. Each policy fetches only what it
needs. Shared reads (README, file list) are memoized per request so three policies do not
trigger three tree calls. In practice: `engine.py` fires all selected policies at once with asyncio.gather. 
They share the one provider instance, so when all three ask for the file list, GitHub is only called once.


### 4.6 Caching on commit SHA

A repository at a given SHA is immutable, so a report at that SHA is valid forever. Cache key
is `(provider, owner, repo, sha, policy_ids)`. In-memory dict for this exercise, Redis with no
expiry should be the production choice. What a cache hit saves: every call spends two cheap requests - 
repo metadata for the default branch, and the branch ref for the sha. Those come back in a few hundred bytes each. 
What the cache skips is everything after: the recursive tree call, which can be megabytes on a large repo.

### 4.7 MCP transport: stdio

Stdio drops into Claude Desktop and Claude Code with no network setup. We can switch to Streamable HTTP 
in FastMCP and it is the production choice (multiple clients, OAuth, load
balancing). Auth is out of scope for now (read-only tool over public repos). It is discussed in §5.5.

### 4.8 Policy heuristics

| Policy | Passes when | Evidence | Known gaps |
|---|---|---|---|
| `ci_tests` | Any of `.github/workflows/*.yml|yaml`, `.circleci/config.yml`, `.gitlab-ci.yml`, `Jenkinsfile`, `.travis.yml`, `azure-pipelines.yml` exists | Matching paths | A workflow that only lints still passes. A `tests/` dir without CI is reported as `passed=false, confidence=low` with a note. |
| `contact_email` | A README (`README`, `README.md`, `README.rst`, any case) located per GitHub's documentation: in the project root, .github/, or docs/ contains an RFC-ish email | Path, line, the address | Excludes `noreply`/`no-reply` and addresses inside image/badge URLs. Nested READMEs elsewhere (vendored code, examples) are ignored. Does not verify deliverability. |
| `package_usage[scikit-learn]` | `scikit-learn`/`sklearn` in `requirements*.txt`, `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile`, `environment.yml`; or `import sklearn` / `from sklearn` in any `.py` | Path, line, snippet | Import scan capped at 200 `.py` files (largest-first is not attempted; first 200 in tree order). Over the cap → `confidence=medium`. Comments and strings can false-positive. |

## 5. Scaling scenarios

### 5.1 More validations
Set up a new `Policy` class and register it, done. Nothing else changes. The report shape already handles N
results.

### 5.2 Other platforms (Bitbucket, GitLab, Azure DevOps)
New `RepositoryProvider`. The engine dispatches on URL host. Policies are untouched because they
only depend on `list_files` and `read_file`. The one leak to watch: CI config file names differ
by platform (`bitbucket-pipelines.yml`), so `ci_tests` grows its pattern list rather than
becoming provider-specific.

### 5.3 Hundreds of repositories
- Current design is already async and cheap per repo (~3-6 HTTP calls).
- Add a second tool that takes a list of repo URLs and checks them all at once, 
  but only a limited number at a time so we don't blast GitHub and hit its rate limit. 
  It returns one overall summary plus the full report for each repo.
- Move the cache to Redis keyed on SHA; most quick re-scans are cache hits because most repos do
  not change between scans.
- For thousands: a message queue could be implemented with the MCP tool submitting and polling, 
  plus webhook-triggered re-evaluation on push so scans happen on change rather than on schedule.
- Rate limits are the real ceiling. A GitHub token per organization scales
  limits with customers rather than sharing one PAT.

### 5.4 User-defined policies
Two tiers:
1. **Parameterized built-ins** (exists now): `PackageUsagePolicy(package="X")`,
   `FilePresencePolicy(pattern=...)`, `ContentMatchPolicy(path_glob=..., regex=...)`. Most
   customer asks ("scan for package X", "require a `SECURITY.md`") are instances of these and
   we can reuse or build upon the existing logic.
2. **Declarative rules**: a JSON/YAML rule referencing a built-in by id with parameters, stored
   per customer/group, loaded into the registry at startup. Should be validated with Pydantic 
   so a bad rule fails at load, not at scan.

Rules that need data outside the repo ("only group Y can access") can be a different provider -
a `PlatformAdminProvider` with `list_collaborators` - not a different policy model. The policy
interface stays the same; the provider grows.

### 5.5 Governance of the tool itself
In production the server would run over Streamable HTTP behind OAuth, with per-tool scopes
(`policies:read`, `repos:scan`) and an audit log of every tool call with caller identity. Evidence
in results is what makes the audit log useful.

### 5.6 Better CI/CD checks
In the future, instead of just validating the expected CI files exist, we can build logic
around reading those files and determining if a CI/CD workflow is actually set up and working.

### 5.7 Better confidence scoring mechanism
This could involve a combination of detailed and reviewed rules in the code + an independent model
we train to determine confidence scores/levels for any and all policies that are in place.

## 6. What I would do next, in order

1. `check_repositories` batch tool with a cionfigurable bounded concurrency.
2. Redis cache.
3. Multiple sources implementing the RepositoryProvider interface (Gitlab, Bitbuket, etc.)
4. Switch to Streamable HTTP transport and add OAuth.
5. Deploy the server to a container with well set up auditing/logging.
6. `ContentMatchPolicy` and `FilePresencePolicy` generics, then the declarative YAML rule loader.
7. Evals: a fixed set of known repos with expected results, run in CI, so heuristic/rule changes 
   are measured rather than eyeballed.

## 7. Out of scope, deliberately

Web framework (Django/FastAPI) - nothing here needs HTTP routing beyond what FastMCP provides.
Database - results are derived and cacheable; persistence belongs with the batch/queue layer.
LLM-assisted checks - tempting for "does the README explain how to report issues", but they
would belong in a separate policy class with careful implementation and evaluation.
