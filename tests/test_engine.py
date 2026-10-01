from repo_policy.engine import Engine
from tests.conftest import FakeProvider

FILES = {
    ".github/workflows/ci.yml": "",
    "README.md": "Contact: team@acme.org\n",
    "requirements.txt": "scikit-learn\n",
}


async def test_second_check_is_served_from_cache() -> None:
    provider = FakeProvider(FILES)
    engine = Engine(providers={"github.com": lambda: provider})

    first = await engine.check("https://github.com/acme/demo")
    calls_after_first = provider.calls
    second = await engine.check("https://github.com/acme/demo")

    assert first.error is None
    assert all(r.passed for r in first.results)
    assert second.results == first.results
    # The hit still resolves the sha (one call) but does no listing or reading.
    assert provider.calls == calls_after_first + 1


async def test_bad_url_is_reported_not_raised() -> None:
    engine = Engine(providers={"github.com": lambda: FakeProvider(FILES)})
    report = await engine.check("https://gitlab.com/acme/demo")
    assert report.error is not None
    assert report.results == []
