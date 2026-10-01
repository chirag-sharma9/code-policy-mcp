# repo-policy

An MCP server that checks a public GitHub repository against governance policies and returns
evidence an AI agent can report. No cloning, no LLM in the evaluation path: a handful of GitHub
API calls, deterministic results, cached by commit.

Policies today:

| id | passes when |
|---|---|
| `ci_tests` | A CI config exists (GitHub Actions workflow, CircleCI, GitLab CI, Jenkinsfile, Travis, Azure Pipelines) |
| `contact_email` | The front-page README contains a contact email |
| `package_usage[scikit-learn]` | `scikit-learn` is declared in a dependency manifest or imported in a `.py` file |

Design and trade-offs are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/chirag-sharma9/code-policy-mcp.git
cd code-policy-mcp
uv sync          # creates .venv and installs everything
uv run pytest    # 12 tests, well under a second
```

## Connect to Claude Desktop

1. Open the config file. In Claude Desktop: **Settings → Developer → Edit Config**. It lives at
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
2. Add the server. Replace the path with the absolute path to your clone.

   ```json
   {
     "mcpServers": {
       "repo-policy": {
         "command": "uv",
         "args": ["--directory", "/ABSOLUTE/PATH/TO/code-policy-mcp", "run", "repo-policy"]
       }
     }
   }
   ```

   If Claude Desktop cannot find `uv`, use its full path as `command` (`which uv` on macOS/Linux,
   `where uv` on Windows).
3. Quit Claude Desktop completely and reopen it. The tools menu in the chat box should list
   `repo-policy` with two tools: `check_repository` and `list_policies`.
4. Try it: *"Check https://github.com/pallets/flask against all repository policies."*

## Connect to Claude Code

```bash
claude mcp add repo-policy -- uv --directory /ABSOLUTE/PATH/TO/code-policy-mcp run repo-policy
```

Then `/mcp` inside Claude Code shows the server and its tools.

## GitHub token (highly recommended)

Unauthenticated calls are limited to 60 per hour, and the scikit-learn import scan can read up
to 200 files on a repository that does not declare it. A token raises the limit to 5,000 per hour.
Create a fine-grained personal access token with read access to public repositories:

```
github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token. Set repository access to "Public repositories"
```
Pass the token to the server:

```json
"repo-policy": {
  "command": "uv",
  "args": ["--directory", "/ABSOLUTE/PATH/TO/code-policy-mcp", "run", "repo-policy"],
  "env": { "GITHUB_TOKEN": "github_pat_..." }
}
```
Then quit Claude Desktop completely and reopen it.

For Claude Code, `export GITHUB_TOKEN=...` in the shell before `claude mcp add`, or add `-e GITHUB_TOKEN=...`.

## Full mcp server cofig section example for claude desktop

```
  "mcpServers": {
    "repo-policy": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/ChiragSharma/trustible/code-policy-mcp",
        "run",
        "repo-policy"
      ],
      "env": {
        "GITHUB_TOKEN": "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"
      }
    }
  },
```

## Example

Real output from Claude Desktop, unauthenticated, against Flask:

```json
{ "url": "https://github.com/pallets/flask" }
```

```json
{
  "url": "https://github.com/pallets/flask",
  "ref": {
    "provider": "github", "owner": "pallets", "name": "flask",
    "default_branch": "main", "sha": "d73fa1cdcbd8b1465c151db8924ba58b1dd14e35"
  },
  "results": [
    {
      "policy_id": "ci_tests",
      "passed": true,
      "confidence": "high",
      "evidence": [
        { "path": ".github/workflows/lock.yaml", "line": null, "snippet": null },
        { "path": ".github/workflows/pre-commit.yaml", "line": null, "snippet": null },
        { "path": ".github/workflows/publish.yaml", "line": null, "snippet": null },
        { "path": ".github/workflows/tests.yaml", "line": null, "snippet": null },
        { "path": ".github/workflows/zizmor.yaml", "line": null, "snippet": null }
      ],
      "notes": "Found 5 CI configuration file(s). Contents were not inspected."
    },
    {
      "policy_id": "contact_email",
      "passed": false,
      "confidence": "high",
      "evidence": [{ "path": "README.md", "line": null, "snippet": null }],
      "notes": "README found but it contains no email address."
    }
  ],
  "error": "package_usage[scikit-learn]: GitHub API rate limit exceeded; resets at unix time 1790828374; set GITHUB_TOKEN to raise the limit"
}
```

The third policy ran out of unauthenticated quota mid-scan (Flask has no scikit-learn, so the
import scan reads many files). The two finished results are kept, the failure is named in
`error`, and the report is not cached so the next call retries. A bad URL or a missing or private
repository produces the same shape with empty `results`. Nothing raises across the tool boundary.

## Running things by hand

```bash
uv run repo-policy                      # start the server on stdio (waits for a client; Ctrl-C to stop)
uv run python scripts/smoke.py          # check three real repos and print full reports
uv run python scripts/smoke.py URL ...  # or your own
uv run pytest -v                        # tests
```

## Layout

```
src/repo_policy/
  server.py        FastMCP server: check_repository, list_policies
  engine.py        URL → provider → resolve sha → cache → run policies → RepoReport
  cache.py         In-memory report cache keyed on commit sha
  models.py        RepoRef, Evidence, PolicyResult, RepoReport
  providers/       RepositoryProvider interface, GitHubProvider (REST, httpx)
  policies/        Policy interface, registry, the three policies
tests/             FakeProvider-backed unit tests
scripts/smoke.py   Live run against real repositories
```
