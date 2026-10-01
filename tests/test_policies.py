from repo_policy.models import RepoRef
from repo_policy.policies.ci_tests import CiTestsPolicy
from repo_policy.policies.contact_email import ContactEmailPolicy
from repo_policy.policies.package_usage import PackageUsagePolicy
from tests.conftest import FakeProvider

ci = CiTestsPolicy()
email = ContactEmailPolicy()
sklearn = PackageUsagePolicy(package="scikit-learn", import_names=["sklearn"])


# ci_tests


async def test_ci_passes_on_workflow_file(ref: RepoRef) -> None:
    result = await ci.evaluate(ref, FakeProvider({".github/workflows/test.yml": "", "app.py": ""}))
    assert result.passed is True
    assert result.confidence == "high"
    assert [e.path for e in result.evidence] == [".github/workflows/test.yml"]


async def test_ci_fails_when_nothing_found(ref: RepoRef) -> None:
    result = await ci.evaluate(ref, FakeProvider({"app.py": "", "README.md": ""}))
    assert result.passed is False
    assert result.confidence == "high"
    assert result.evidence == []


async def test_ci_tests_dir_without_ci_is_low_confidence(ref: RepoRef) -> None:
    result = await ci.evaluate(ref, FakeProvider({"tests/test_app.py": "", "app.py": ""}))
    assert result.passed is False
    assert result.confidence == "low"
    assert [e.path for e in result.evidence] == ["tests/"]


# contact_email


async def test_email_passes_on_readme_address(ref: RepoRef) -> None:
    readme = "# Demo\n\nQuestions? Email maintainers@acme.org\n"
    result = await email.evaluate(ref, FakeProvider({"README.md": readme}))
    assert result.passed is True
    assert result.confidence == "high"
    assert result.evidence[0].path == "README.md"
    assert result.evidence[0].line == 3
    assert result.evidence[0].snippet == "maintainers@acme.org"


async def test_email_fails_without_readme(ref: RepoRef) -> None:
    result = await email.evaluate(ref, FakeProvider({"app.py": "", "docs/guide.md": "x@y.io"}))
    assert result.passed is False
    assert result.confidence == "high"


async def test_email_only_noreply_or_badge_is_low_confidence(ref: RepoRef) -> None:
    readme = "![mail](https://img.shields.io/badge/mail-a@b.com-blue)\nSent by noreply@github.com\n"
    result = await email.evaluate(ref, FakeProvider({"README.md": readme}))
    assert result.passed is False
    assert result.confidence == "low"
    assert [e.snippet for e in result.evidence] == ["noreply@github.com"]


# package_usage[scikit-learn]


async def test_package_passes_on_manifest(ref: RepoRef) -> None:
    files = {"requirements.txt": "numpy\nscikit-learn>=1.4\n", "app.py": "import numpy\n"}
    result = await sklearn.evaluate(ref, FakeProvider(files))
    assert result.passed is True
    assert result.confidence == "high"
    assert result.evidence[0].path == "requirements.txt"
    assert result.evidence[0].line == 2
    assert result.evidence[0].snippet == "scikit-learn>=1.4"


async def test_package_passes_on_import(ref: RepoRef) -> None:
    files = {"requirements.txt": "numpy\n", "model.py": "from sklearn.linear_model import Ridge\n"}
    result = await sklearn.evaluate(ref, FakeProvider(files))
    assert result.passed is True
    assert result.evidence[0].path == "model.py"


async def test_package_fails_when_absent(ref: RepoRef) -> None:
    files = {"requirements.txt": "numpy\nsklearnex\n", "app.py": "x = 'sklearn'\n"}
    result = await sklearn.evaluate(ref, FakeProvider(files))
    assert result.passed is False
    assert result.confidence == "high"


async def test_package_over_scan_cap_is_medium_confidence(ref: RepoRef) -> None:
    files = {f"m{i:03}.py": "import os\n" for i in range(201)}
    result = await sklearn.evaluate(ref, FakeProvider(files))
    assert result.passed is False
    assert result.confidence == "medium"
    assert "200 of 201" in (result.notes or "")
