"""Tests for the drafting stage that never touch the API.

Run: python tests/test_draft.py

The safety claim this tool is sold on is that it will not invent a capability.
That claim rests on two mechanisms, both testable offline:

  * a requirement with no evidence is never sent to the model at all, and
  * a requirement whose answer the model declines to write is reported as
    undrafted rather than quietly dropped.

Both are asserted here. Nothing in this file makes a network call.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from biddesk import docparse, draft, extract, report, retrieve  # noqa: E402

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")
        failures.append(label)


def check_in(label: str, needle: str, haystack: str) -> None:
    if needle in haystack:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          {needle!r} not found")
        failures.append(label)


def check_not_in(label: str, needle: str, haystack: str) -> None:
    if needle not in haystack:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          {needle!r} should be absent")
        failures.append(label)


class FakeBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.content = [FakeBlock(text)] if text else []
        self.stop_reason = stop_reason


def build_results():
    """Score a small tender so the assessments are real, not hand-built."""
    corpus_text = (
        "# Security\n"
        "We hold ISO 27001 certification, certificate IS-694412, and encrypt all "
        "data at rest using AES-256 with quarterly key rotation.\n"
        "# Service Desk\n"
        "Our service desk operates 24 hours a day, 365 days a year from Manchester "
        "and Glasgow, with all analysts UK-based.\n"
    )
    tender = (
        "4.1.1 The Supplier shall hold ISO 27001 certification throughout the term.\n"
        "3.1.1 The Supplier shall operate a service desk available 24 hours per day.\n"
        "5.1.2 The Supplier shall provide service desk support in Welsh and English.\n"
        "6.1.1 The Supplier shall submit a Carbon Reduction Plan for Net Zero by 2050.\n"
    )
    tmp = Path(tempfile.mkdtemp())
    (tmp / "corpus").mkdir()
    (tmp / "corpus" / "past.md").write_text(corpus_text, encoding="utf-8")

    chunks, _ = retrieve.load_corpus(tmp / "corpus")
    index = retrieve.Index(chunks)
    requirements = extract.extract(docparse.normalise(tender))
    return report.assess(requirements, index)


def main() -> int:
    results = build_results()
    by_ref = {item.requirement.ref: item for item in results}

    print("gaps are never sent to the model")
    targets = draft.draftable(results)
    target_refs = {item.requirement.ref for item in targets}
    gap_refs = {item.requirement.ref for item in results if item.verdict == report.GAP}

    check("some requirements are covered", len(targets) > 0, True)
    check("some requirements are gaps", len(gap_refs) > 0, True)
    check("no gap is draftable", target_refs & gap_refs, set())
    check("every target has evidence", all(item.evidence for item in targets), True)

    prompt = draft.build_prompt(targets)
    for ref in gap_refs:
        item = by_ref[ref]
        check_not_in(f"gap {ref} absent from prompt", item.requirement.text[:50], prompt)

    print("\nprompt grounding")
    check_in("requirement text included", "ISO 27001 certification throughout", prompt)
    check_in("evidence included", "IS-694412", prompt)
    check_in("evidence is attributed", "past.md", prompt)
    check_in("system prompt forbids invention", "never state a capability",
             draft.SYSTEM.lower())
    check_in("system prompt allows refusal", "evidence_sufficient to false",
             draft.SYSTEM)

    print("\nresponse parsing")
    batch = targets[:2]
    payload = (
        '{"drafts": ['
        f'{{"id": "{batch[0].requirement.id}", "answer": "The Supplier maintains ISO 27001.",'
        ' "evidence_sufficient": true, "note": ""},'
        f'{{"id": "{batch[1].requirement.id}", "answer": "",'
        ' "evidence_sufficient": false, "note": "No evidence of Welsh provision."}'
        ']}'
    )
    drafts = draft._parse(FakeResponse(payload), batch)
    check("one draft per requirement", len(drafts), 2)
    check("sufficient flag read", drafts[0].evidence_sufficient, True)
    check("refusal preserved", drafts[1].evidence_sufficient, False)
    check("refusal reason preserved", drafts[1].note, "No evidence of Welsh provision.")

    print("\na dropped requirement is reported, not lost")
    partial = (
        '{"drafts": [{"id": "%s", "answer": "Yes.", "evidence_sufficient": true, "note": ""}]}'
        % batch[0].requirement.id
    )
    drafts = draft._parse(FakeResponse(partial), batch)
    check("still one entry per requirement", len(drafts), 2)
    missing = next(d for d in drafts if d.requirement_id == batch[1].requirement.id)
    check("missing entry marked insufficient", missing.evidence_sufficient, False)
    check_in("missing entry explains itself", "no draft", missing.note.lower())

    print("\nAPI failures surface as actionable errors")
    for label, response, needle in (
        ("empty response", FakeResponse("", "max_tokens"), "batch-size"),
        ("malformed json", FakeResponse("not json at all"), "parse"),
    ):
        try:
            draft._parse(response, batch)
            check(label, "no error", "DraftingUnavailable")
        except draft.DraftingUnavailable as exc:
            check_in(label, needle, str(exc).lower())

    print("\nwritten output separates drafted from refused")
    drafts = [
        draft.Draft(batch[0].requirement.id, "The Supplier maintains ISO 27001.", True, ""),
        draft.Draft(batch[1].requirement.id, "", False, "No evidence of Welsh provision."),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "drafts.md"
        draft.write_markdown(drafts, results, path)
        body = path.read_text(encoding="utf-8")
        check_in("refusals get their own section", "Not drafted", body)
        check_in("refusal reason shown", "No evidence of Welsh provision", body)
        check_in("draft body present", "The Supplier maintains ISO 27001", body)
        check_in("evidence cited", "past.md", body)
        check_in("human review flagged", "checked by a person", body)

    print("\npreview builds prompts without a client or a call")
    prompts = draft.preview(results, batch_size=5)
    check("one batch for these requirements", len(prompts), 1)
    ids, text = prompts[0]
    check("preview covers every draftable requirement", len(ids), len(targets))
    check_in("preview returns the real prompt", "IS-694412", text)
    check("preview returns text, does not print", isinstance(text, str), True)

    print()
    if failures:
        print(f"  FAILED ({len(failures)}): {failures}")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
