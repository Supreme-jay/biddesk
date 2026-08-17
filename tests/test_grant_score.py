"""Test grant scoring and source tagging.

Run: python tests/test_grant_score.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biddesk.retrieve import load_corpus, Index  # noqa: E402
from grantdesk import extract  # noqa: E402
from grantdesk.score import score_all, EVIDENCED_AT  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")
        failures.append(label)


def check_true(label: str, value) -> None:
    check(label, bool(value), True)


def main() -> int:
    from biddesk import docparse

    raw = docparse.read(SAMPLES / "grants" / "innovation-fund-2026.md")
    lines = docparse.normalise(raw)
    sections = extract.extract(lines)
    chunks, _ = load_corpus(SAMPLES / "corpus")
    index = Index(chunks)
    results = score_all(sections, index)

    print("scoring")
    check_true("results returned", len(results) > 0)
    check("one result per section", len(results), len(sections))

    print("\nsource tagging")
    for r in results:
        tag = r.source_tag
        if r.coverage >= EVIDENCED_AT:
            check(f"{r.section.id} tagged EVIDENCED at {r.coverage:.2f}", tag, "EVIDENCED")
        else:
            check(f"{r.section.id} tagged GENERATED at {r.coverage:.2f}", tag, "GENERATED")

    print("\ncoverage bounds")
    for r in results:
        check_true(f"{r.section.id} coverage >= 0", r.coverage >= 0.0)
        check_true(f"{r.section.id} coverage <= 1", r.coverage <= 1.0)

    print("\nempty index")
    empty_index = Index([])
    empty_results = score_all(sections[:1], empty_index)
    check("empty index gives zero coverage", empty_results[0].coverage, 0.0)
    check("empty index tags GENERATED", empty_results[0].source_tag, "GENERATED")

    print()
    if failures:
        print(f"  FAILED ({len(failures)}): {failures}")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
