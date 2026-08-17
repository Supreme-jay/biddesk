"""Test local drafting engine — no network, no API key.

Run: python tests/test_grant_localdraft.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biddesk import docparse  # noqa: E402
from biddesk.retrieve import load_corpus, Index  # noqa: E402
from grantdesk import extract  # noqa: E402
from grantdesk.score import score_all  # noqa: E402
from grantdesk import localdraft  # noqa: E402

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


def _load():
    raw = docparse.read(SAMPLES / "grants" / "innovation-fund-2026.md")
    lines = docparse.normalise(raw)
    sections = extract.extract(lines)
    chunks, _ = load_corpus(SAMPLES / "corpus")
    index = Index(chunks)
    return score_all(sections, index)


def main() -> int:
    results = _load()

    print("all sections drafted")
    drafts = localdraft.generate(results)
    check("one draft per section", len(drafts), len(results))

    print("\nsource tags preserved")
    for d, r in zip(drafts, results):
        check(f"{d.section_id} tag matches", d.source_tag, r.source_tag)

    print("\nevidenced sections use evidence")
    ev_drafts = [d for d in drafts if d.source_tag == "EVIDENCED"]
    check_true("at least one evidenced draft", len(ev_drafts) > 0)
    for d in ev_drafts:
        check_true(f"{d.section_id} has content", len(d.answer) > 20)

    print("\ngenerated sections have placeholders")
    gen_drafts = [d for d in drafts if d.source_tag == "GENERATED"]
    check_true("generated drafts exist", len(gen_drafts) > 0)
    for d in gen_drafts:
        check_true(f"{d.section_id} has PLACEHOLDER", "[PLACEHOLDER" in d.answer)
        check_true(f"{d.section_id} has placeholder list", len(d.placeholders) > 0)

    print("\nquestion-type detection")
    all_answers = " ".join(d.answer for d in gen_drafts)
    check_true("risk table generated", "Risk" in all_answers or "Mitigation" in all_answers)
    check_true("team structure generated", "team" in all_answers.lower() or "led by" in all_answers.lower())
    check_true("budget table generated", "Budget" in all_answers or "Staff costs" in all_answers)

    print("\nno draft is empty")
    for d in drafts:
        check_true(f"{d.section_id} not empty", len(d.answer.strip()) > 0)

    print()
    if failures:
        print(f"  FAILED ({len(failures)}): {failures}")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
