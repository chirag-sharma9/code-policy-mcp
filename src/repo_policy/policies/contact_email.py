import re

from repo_policy.models import Evidence, PolicyResult, RepoRef
from repo_policy.policies.base import Policy
from repo_policy.policies.registry import registry
from repo_policy.providers.base import RepositoryProvider

_README_NAMES = frozenset({"readme", "readme.md", "readme.rst"})
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_NOREPLY = re.compile(r"no-?reply", re.IGNORECASE)
_URL = re.compile(r"https?://\S+")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>|^\s*\.\.\s+image::.*$", re.IGNORECASE | re.MULTILINE)


class ContactEmailPolicy(Policy):
    id = "contact_email"
    description = "A README exists at the repository root and contains a contact email address."

    async def evaluate(self, ref: RepoRef, provider: RepositoryProvider) -> PolicyResult:
        files = await provider.list_files(ref)
        readmes = sorted(p for p in files if "/" not in p and p.lower() in _README_NAMES)
        if not readmes:
            return PolicyResult(
                policy_id=self.id, passed=False, confidence="high", notes="No README found at the repository root."
            )

        found: list[Evidence] = []
        excluded: list[Evidence] = []
        for path in readmes:
            text = await provider.read_file(ref, path)
            if text is None:
                continue
            for line_no, line in enumerate(_IMAGE.sub(" ", text).splitlines(), start=1):
                url_spans = [m.span() for m in _URL.finditer(line)]
                for match in _EMAIL.finditer(line):
                    evidence = Evidence(path=path, line=line_no, snippet=match.group())
                    in_url = any(start <= match.start() < end for start, end in url_spans)
                    (excluded if in_url or _NOREPLY.search(match.group()) else found).append(evidence)

        if found:
            return PolicyResult(
                policy_id=self.id,
                passed=True,
                confidence="high",
                evidence=found,
                notes="Deliverability was not verified.",
            )
        if excluded:
            return PolicyResult(
                policy_id=self.id,
                passed=False,
                confidence="low",
                evidence=excluded,
                notes="Only no-reply addresses or addresses inside badge/image URLs were found; review manually.",
            )
        return PolicyResult(
            policy_id=self.id,
            passed=False,
            confidence="high",
            evidence=[Evidence(path=p) for p in readmes],
            notes="README found but it contains no email address.",
        )


registry.register(ContactEmailPolicy())
