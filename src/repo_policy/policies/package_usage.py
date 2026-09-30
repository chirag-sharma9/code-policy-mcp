import asyncio
import fnmatch
import re
from collections.abc import Sequence

from repo_policy.models import Confidence, Evidence, PolicyResult, RepoRef
from repo_policy.policies.base import Policy
from repo_policy.policies.registry import registry
from repo_policy.providers.base import RepositoryProvider

DEFAULT_MANIFESTS: tuple[str, ...] = (
    "requirements*.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "environment.yml",
)
IMPORT_SCAN_CAP = 200
_READ_BATCH = 10
_SNIPPET_LEN = 120


class PackageUsagePolicy(Policy):
    """Parameterized check that ``package`` is declared in a manifest or imported in source."""

    def __init__(
        self,
        package: str,
        import_names: Sequence[str],
        manifests: Sequence[str] = DEFAULT_MANIFESTS,
        import_scan_cap: int = IMPORT_SCAN_CAP,
    ) -> None:
        self.package = package
        self.import_names = tuple(import_names)
        self.manifests = tuple(manifests)
        self.import_scan_cap = import_scan_cap
        self.id = f"package_usage[{package}]"
        self.description = (
            f"The {package!r} package is used: declared in a dependency manifest "
            f"({', '.join(self.manifests)}) or imported in a .py file."
        )
        # pip treats '-' and '_' as equivalent in distribution names.
        names = [re.escape(package).replace(r"\-", "[-_]"), *map(re.escape, self.import_names)]
        self._manifest_re = re.compile(r"(?<![\w-])(?:" + "|".join(names) + r")(?![\w-])", re.IGNORECASE)
        self._import_re = re.compile(
            r"^\s*(?:import\s+(?:[\w.]+\s*,\s*)*|from\s+)(?:" + "|".join(map(re.escape, self.import_names)) + r")\b"
        )

    async def evaluate(self, ref: RepoRef, provider: RepositoryProvider) -> PolicyResult:
        files = await provider.list_files(ref)
        truncated = await provider.tree_truncated(ref)

        manifests = [p for p in files if any(fnmatch.fnmatch(p.rsplit("/", 1)[-1], pat) for pat in self.manifests)]
        declared = await self._scan(ref, provider, manifests, self._manifest_re)
        if declared:
            return PolicyResult(
                policy_id=self.id,
                passed=True,
                confidence="high",
                evidence=declared,
                notes=f"{self.package} is declared in a dependency manifest; import scan skipped.",
            )

        py_files = [p for p in files if p.endswith(".py")]
        scanned = py_files[: self.import_scan_cap]
        imported = await self._scan(ref, provider, scanned, self._import_re, stop_on_hit=True)
        if imported:
            return PolicyResult(
                policy_id=self.id,
                passed=True,
                confidence="high",
                evidence=imported,
                notes=f"{self.package} is not declared in any manifest ({len(manifests)} checked) but is imported.",
            )

        capped = len(py_files) > len(scanned)
        confidence: Confidence = "medium" if (capped or truncated) else "high"
        notes = f"No reference to {self.package} in {len(manifests)} manifest(s) or {len(scanned)} .py file(s)."
        if capped:
            notes += f" Only the first {self.import_scan_cap} of {len(py_files)} .py files were scanned."
        if truncated:
            notes += " File listing was truncated, so files may have been missed."
        return PolicyResult(policy_id=self.id, passed=False, confidence=confidence, notes=notes)

    async def _scan(
        self,
        ref: RepoRef,
        provider: RepositoryProvider,
        paths: list[str],
        pattern: re.Pattern[str],
        *,
        stop_on_hit: bool = False,
    ) -> list[Evidence]:
        found: list[Evidence] = []
        for start in range(0, len(paths), _READ_BATCH):
            batch = paths[start : start + _READ_BATCH]
            contents = await asyncio.gather(*(provider.read_file(ref, p) for p in batch))
            for path, text in zip(batch, contents):
                if text is None:
                    continue
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if pattern.search(line):
                        found.append(Evidence(path=path, line=line_no, snippet=_snippet(line)))
            if found and stop_on_hit:
                break
        return found


def _snippet(line: str) -> str:
    line = line.strip()
    return line if len(line) <= _SNIPPET_LEN else line[: _SNIPPET_LEN - 1] + "…"


registry.register(PackageUsagePolicy(package="scikit-learn", import_names=("sklearn",)))
