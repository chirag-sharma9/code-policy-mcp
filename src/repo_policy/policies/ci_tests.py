import re

from repo_policy.models import Evidence, PolicyResult, RepoRef
from repo_policy.policies.base import Policy
from repo_policy.policies.registry import registry
from repo_policy.providers.base import RepositoryProvider

# GitHub only runs workflows that are direct children of .github/workflows.
_WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
_CI_FILES = frozenset(
    {".circleci/config.yml", ".gitlab-ci.yml", "Jenkinsfile", ".travis.yml", "azure-pipelines.yml"}
)
_TEST_DIRS = frozenset({"tests", "test"})


class CiTestsPolicy(Policy):
    id = "ci_tests"
    description = (
        "Automated tests are set up: a CI configuration exists (GitHub Actions workflow, "
        "CircleCI, GitLab CI, Jenkinsfile, Travis or Azure Pipelines)."
    )

    async def evaluate(self, ref: RepoRef, provider: RepositoryProvider) -> PolicyResult:
        files = await provider.list_files(ref)
        ci_paths = sorted(p for p in files if p in _CI_FILES or _WORKFLOW.match(p))
        if ci_paths:
            return PolicyResult(
                policy_id=self.id,
                passed=True,
                confidence="high",
                evidence=[Evidence(path=p) for p in ci_paths],
                notes=f"Found {len(ci_paths)} CI configuration file(s). Contents were not inspected.",
            )

        truncated = await provider.tree_truncated(ref)
        test_dirs = sorted({_test_dir(p) for p in files} - {None})
        if test_dirs:
            return PolicyResult(
                policy_id=self.id,
                passed=False,
                confidence="low",
                evidence=[Evidence(path=f"{d}/") for d in test_dirs],
                notes="A test directory exists but no CI configuration was found; tests may run only manually."
                + (" File listing was truncated, so a CI file may have been missed." if truncated else ""),
            )
        return PolicyResult(
            policy_id=self.id,
            passed=False,
            confidence="medium" if truncated else "high",
            notes="No CI configuration or test directory found."
            + (" File listing was truncated, so files may have been missed." if truncated else ""),
        )


def _test_dir(path: str) -> str | None:
    """Return the leading directory path up to a ``tests``/``test`` segment, or None."""
    parts = path.split("/")
    for i, part in enumerate(parts[:-1]):
        if part in _TEST_DIRS:
            return "/".join(parts[: i + 1])
    return None


registry.register(CiTestsPolicy())
