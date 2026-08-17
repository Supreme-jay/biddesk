"""Test grant-specific section extraction.

Run: python tests/test_grant_extract.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grantdesk import extract  # noqa: E402

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")
        failures.append(label)


def check_true(label: str, value) -> None:
    check(label, bool(value), True)


def check_false(label: str, value) -> None:
    check(label, bool(value), False)


def main() -> int:
    print("prompt detection")
    lines = [
        "1.1) Describe your organisation and its relevant experience. (max 300 words, worth 10 marks)",
        "1.2) What problem does your project address? (max 500 words)",
        "2.1) Provide a detailed project plan including milestones.",
        "This document sets out the background to the fund.",
        "Page 4 of 22",
        "3.1) How will you measure the success and impact of your project?",
        "Explain your risk mitigation strategy.",
        "APPLICATION FORM",
    ]
    sections = extract.extract(lines)
    ids = {s.id for s in sections}
    check_true("describe prompt extracted", any("Describe your organisation" in s.text for s in sections))
    check_true("what question extracted", any("What problem" in s.text for s in sections))
    check_true("provide prompt extracted", any("Provide a detailed" in s.text for s in sections))
    check_true("how question extracted", any("How will you measure" in s.text for s in sections))
    check_true("explain prompt extracted", any("Explain your risk" in s.text for s in sections))
    check_false("background prose rejected", any("sets out the background" in s.text for s in sections))
    check_false("page number rejected", any("Page 4" in s.text for s in sections))
    check_false("all-caps heading rejected", any("APPLICATION" in s.text for s in sections))

    print("\nword limits")
    wl_sections = [s for s in sections if s.word_limit]
    check_true("word limits captured", len(wl_sections) >= 2)
    first = next((s for s in sections if "Describe your organisation" in s.text), None)
    if first:
        check("300 word limit parsed", first.word_limit, 300)

    print("\nscoring weights")
    sw_sections = [s for s in sections if s.score_weight]
    check_true("score weights captured", len(sw_sections) >= 1)
    if first:
        check_true("10 marks weight parsed", "10" in first.score_weight)

    print("\nsection references")
    refs = {s.ref for s in sections if s.ref}
    check_true("1.1 ref parsed", "1.1" in refs)
    check_true("1.2 ref parsed", "1.2" in refs)

    print("\nclassification")
    q = next((s for s in sections if "What problem" in s.text), None)
    if q:
        check("question classified as question", q.kind, "question")

    print("\ndeduplication")
    dupes = extract.extract([
        "Describe your approach to data security.",
        "Describe your approach to data security.",
    ])
    check("duplicates collapsed", len(dupes), 1)

    print("\nedge cases")
    check("empty input", extract.extract([]), [])
    check("whitespace only", extract.extract(["   ", ""]), [])

    print("\nsummarise")
    stats = extract.summarise(sections)
    check_true("total matches", stats["total"] == len(sections))
    check_true("has narrative count", "narrative" in stats)
    check_true("has questions count", "questions" in stats)

    print()
    if failures:
        print(f"  FAILED ({len(failures)}): {failures}")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
