from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# high: the verdict rests on concrete evidence, or on a complete search that found nothing.
# medium: the search was incomplete (truncated file listing or capped scan), so a fail may be wrong.
# low: something related was found that the rule cannot count; a human should look.
Confidence = Literal["high", "medium", "low"]


class RepoRef(BaseModel):
    """A repository pinned to one commit. Immutable so it can be part of a cache key."""

    model_config = ConfigDict(frozen=True)

    provider: str = "github"
    owner: str
    name: str
    default_branch: str
    sha: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class Evidence(BaseModel):
    path: str
    line: int | None = None
    snippet: str | None = None


class PolicyResult(BaseModel):
    policy_id: str
    passed: bool
    confidence: Confidence
    evidence: list[Evidence] = Field(default_factory=list)
    notes: str | None = None


class RepoReport(BaseModel):
    url: str
    ref: RepoRef | None = None
    results: list[PolicyResult] = Field(default_factory=list)
    error: str | None = None
