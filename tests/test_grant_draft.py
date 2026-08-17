"""Test grant drafting safety properties — no network calls.

Run: python tests/test_grant_draft.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biddesk.retrieve import load_corpus, Index, tokenise  # noqa: E402
from grantdesk import extract  # noqa: E402
from grantdesk.score import score_all, ScoredSection  # noqa: E402
from grantdesk import draft as drafting  # noqa: E402

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
    from biddesk import docparse
    raw = docparse.read(SAMPLES / "grants" / "innovation-fund-2026.md")
    lines = docparse.normalise(raw)
    sections = extract.extract(lines)
    chunks, _ = load_corpus(SAMPLES / "corpus")
    index = Index(chunks)
    return score_all(sections, index)


def main() -> int:
    results = _load()

    print("all sections are draftable")
    check_true("some sections found", len(results) > 0)
    ev = [r for r in results if r.source_tag == "EVIDENCED"]
    gen = [r for r in results if r.source_tag == "GENERATED"]
    check_true("some evidenced", len(ev) > 0)
    check_true("some generated", len(gen) > 0)

    print("\nprompt construction — evidenced")
    ev_batch = ev[:2] if len(ev) >= 2 else ev
    prompt = drafting.build_prompt_evidenced(ev_batch)
    for item in ev_batch:
        check_true(f"{item.section.id} in evidenced prompt", item.section.id in prompt)
    check_true("extracts included", "Extract" in prompt)

    print("\nprompt construction — generated")
    gen_batch = gen[:2] if len(gen) >= 2 else gen
    prompt = drafting.build_prompt_generated(gen_batch)
    for item in gen_batch:
        check_true(f"{item.section.id} in generated prompt", item.section.id in prompt)
    check_true("placeholder instruction included", "PLACEHOLDER" in prompt)

    print("\nbatching covers all sections")
    all_batches = drafting.batches(results, batch_size=3)
    all_ids = set()
    for kind, batch in all_batches:
        for item in batch:
            all_ids.add(item.section.id)
    result_ids = {r.section.id for r in results}
    check("all sections in batches", all_ids, result_ids)

    print("\npreview returns text without calling API")
    previews = drafting.preview(results, batch_size=3)
    check_true("preview returns entries", len(previews) > 0)
    for kind, ids, prompt_text in previews:
        check_true(f"preview {kind} has prompt text", len(prompt_text) > 50)

    print()
    if failures:
        print(f"  FAILED ({len(failures)}): {failures}")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
