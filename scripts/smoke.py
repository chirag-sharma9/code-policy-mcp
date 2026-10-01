"""Run every policy against real repositories and print the reports.

Usage: uv run python scripts/smoke.py [URL ...]
Set GITHUB_TOKEN to avoid the 60 requests/hour unauthenticated limit.
"""

import asyncio
import sys

import httpx

from repo_policy.engine import Engine

DEFAULT_URLS = [
    "https://github.com/scikit-learn/scikit-learn",
    "https://github.com/pallets/flask",
    "https://github.com/torvalds/pesconvert",
]


async def main(urls: list[str]) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        engine = Engine(client)
        for url in urls:
            report = await engine.check(url)
            print(f"\n=== {url}")
            if report.ref:
                print(f"    {report.ref.default_branch} @ {report.ref.sha[:12]}")
            for r in report.results:
                verdict = "PASS" if r.passed else "FAIL"
                where = ", ".join(f"{e.path}" + (f":{e.line}" if e.line else "") for e in r.evidence[:3])
                print(f"    {verdict}  {r.policy_id:30} confidence={r.confidence:6} {where}")
                if r.notes:
                    print(f"          {r.notes}")
            if report.error:
                print(f"    ERROR {report.error}")
            print("\n" + report.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT_URLS))
