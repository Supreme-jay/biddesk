"""Measure classifier quality against hand-labelled ground truth.

Run: python tests/evaluate.py

The two error classes are not equally bad and are scored separately:

  * FALSE CONFIDENCE -- a real gap marked ANSWERED. This is the bid-losing
    error: the team never reviews it and an unbacked commitment ships in a
    binding document. Target is zero, always.
  * FALSE GAP -- covered material marked GAP. Wastes reviewer time and erodes
    trust in the report, but is recoverable. Tolerated at a low rate.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biddesk import docparse, extract, report, retrieve  # noqa: E402

RFP = Path("samples/rfp/ITT-2026-041-managed-it.md")
CORPUS = Path("samples/corpus")

# Hand-labelled from the sample corpus. COVERED = the proposal library contains
# material that genuinely answers this. GAP = it does not.
COVERED = {
    "3.1.1", "3.1.2", "3.1.3", "3.1.4", "3.1.5", "3.1.6", "3.1.7", "3.1.8",
    "4.1.1", "4.1.3", "4.1.4", "4.1.5", "4.1.6", "4.1.8", "4.1.10",
    "7.1.1", "7.1.2", "7.1.3", "7.1.4", "7.1.5",
    "8.1.1", "8.1.2", "8.1.3",
}
# Related material exists but stops short of the commitment being demanded.
# Either WEAK or GAP is defensible; ANSWERED is not.
PARTIAL = {"4.1.7"}
GAP = {
    "4.1.2", "4.1.9",                                        # Cyber Essentials, FOI
    "5.1.1", "5.1.2", "5.1.3", "5.1.4",                      # accessibility, Welsh
    "6.1.1", "6.1.2", "6.1.3", "6.1.4", "6.1.5", "6.1.6", "6.1.7", "6.1.8",
    "8.1.4",                                                 # rate card
}


def main() -> int:
    lines = docparse.normalise(docparse.read(RFP))
    requirements = extract.extract(lines)
    chunks, skipped = retrieve.load_corpus(CORPUS)
    if skipped:
        print(f"FAIL: corpus files unreadable: {skipped}")
        return 1
    index = retrieve.Index(chunks)
    results = {a.requirement.ref: a for a in report.assess(requirements, index)}

    labelled = COVERED | PARTIAL | GAP
    missing = labelled - set(results)
    if missing:
        print(f"FAIL: labelled requirements not extracted: {sorted(missing)}")
        return 1

    false_confidence = sorted(r for r in GAP if results[r].verdict == report.ANSWERED)
    false_confidence += sorted(r for r in PARTIAL if results[r].verdict == report.ANSWERED)
    false_gaps = sorted(r for r in COVERED if results[r].verdict == report.GAP)
    correct_gaps = [r for r in GAP if results[r].verdict == report.GAP]
    correct_covered = [r for r in COVERED if results[r].verdict != report.GAP]

    print(f"  Extracted            {len(requirements)} requirements")
    print(f"  Labelled             {len(labelled)}")
    print()
    print(f"  Gap recall           {len(correct_gaps)}/{len(GAP)} "
          f"({len(correct_gaps) / len(GAP):.0%}) real gaps flagged as GAP")
    print(f"  Covered recall       {len(correct_covered)}/{len(COVERED)} "
          f"({len(correct_covered) / len(COVERED):.0%}) covered items not lost to false gaps")
    print()
    print(f"  FALSE CONFIDENCE     {len(false_confidence)}  {false_confidence or ''}")
    print(f"  FALSE GAPS           {len(false_gaps)}  {false_gaps or ''}")
    if false_gaps:
        print()
        for ref in false_gaps:
            item = results[ref]
            print(f"    {ref} cov={item.coverage:.2f}  {item.requirement.text[:64]}")
            print(f"         best: {item.citation}")

    print()
    ok = True
    if false_confidence:
        print("  FAIL: real gaps marked ANSWERED — unbacked commitments would ship")
        ok = False
    if len(false_gaps) > 2:
        print(f"  FAIL: {len(false_gaps)} false gaps exceeds tolerance of 2")
        ok = False
    if ok:
        print("  PASS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
