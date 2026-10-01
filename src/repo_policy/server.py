from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from mcp.server.fastmcp import Context, FastMCP

from repo_policy.engine import Engine
from repo_policy.models import RepoReport
from repo_policy.policies import registry

HTTP_TIMEOUT_SECONDS = 30.0


@dataclass
class AppContext:
    engine: Engine


@asynccontextmanager
async def lifespan(_: FastMCP) -> AsyncGenerator[AppContext, None]:
    # One client and one engine per process: connection reuse plus a cache that spans tool calls.
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        yield AppContext(engine=Engine(client))


mcp = FastMCP(
    "repo-policy",
    instructions=(
        "Deterministic governance checks for public GitHub repositories. Call list_policies to "
        "discover checks, then check_repository with a repository URL. Results carry evidence "
        "(file paths, lines, snippets) and a confidence level; report them to the user as found."
    ),
    lifespan=lifespan,
)


@mcp.tool()
async def check_repository(url: str, policies: list[str] | None = None, *, ctx: Context) -> RepoReport:
    """Evaluate a public GitHub repository against governance policies and return a RepoReport.
    Use this when asked whether a repository has CI/automated tests set up, whether its README
    lists a contact email, or whether it uses a particular package such as scikit-learn. Pass the
    repository URL (https://github.com/owner/repo); omit `policies` to run every registered policy,
    or pass a list of policy ids from list_policies to run a subset. The report pins the exact
    commit checked (`ref.sha`) and contains one result per policy with `passed`, a `confidence` of
    high/medium/low, `evidence` naming the files and lines that justified the verdict, and `notes`
    explaining gaps. Evaluation is deterministic and uses the GitHub API, not an LLM. If the URL is
    invalid, the repository is missing or private, or the GitHub rate limit is hit, the report is
    still returned with `error` set and `results` possibly partial; relay that error instead of
    retrying. Repeated calls for an unchanged repository are served from a cache.
    """
    engine: Engine = ctx.request_context.lifespan_context.engine
    return await engine.check(url, policies)


@mcp.tool()
def list_policies() -> list[dict[str, str]]:
    """List the governance policies this server can evaluate, as objects with `id` and
    `description`. Call this first when the user asks what can be checked, or to obtain valid ids
    for the `policies` argument of check_repository. The list is static for the server's lifetime
    and costs no network calls.
    """
    return [{"id": p.id, "description": p.description} for p in registry.all()]


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
