"""Command line entry point: python -m biddesk --rfp <file> --corpus <dir>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import docparse, extract, report, retrieve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="biddesk",
        description="Score an incoming RFP against your library of past proposals.",
    )
    parser.add_argument("--rfp", required=True, type=Path,
                        help="Incoming RFP / tender / questionnaire (.docx .xlsx .md .txt)")
    parser.add_argument("--corpus", required=True, type=Path,
                        help="Directory of past proposals and capability statements")
    parser.add_argument("--out", type=Path, default=Path("out"),
                        help="Output directory (default: ./out)")
    parser.add_argument("--answered-at", type=float, default=report.ANSWERED_AT,
                        help=f"Coverage at or above which a requirement counts as "
                             f"answered (default {report.ANSWERED_AT})")
    parser.add_argument("--weak-at", type=float, default=report.WEAK_AT,
                        help=f"Coverage below which a requirement is a gap "
                             f"(default {report.WEAK_AT})")
    args = parser.parse_args(argv)

    if not args.rfp.is_file():
        parser.error(f"RFP not found: {args.rfp}")
    if not args.corpus.is_dir():
        parser.error(f"Corpus directory not found: {args.corpus}")
    if args.out.exists() and not args.out.is_dir():
        parser.error(f"--out must be a directory, but {args.out} is a file")
    if not 0.0 < args.weak_at < args.answered_at <= 1.0:
        parser.error("thresholds must satisfy 0 < --weak-at < --answered-at <= 1")

    report.ANSWERED_AT = args.answered_at
    report.WEAK_AT = args.weak_at

    try:
        raw = docparse.read(args.rfp)
    except docparse.UnsupportedDocument as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    lines = docparse.normalise(raw)
    if not lines:
        print(f"error: {args.rfp.name} contains no readable text.", file=sys.stderr)
        return 3

    requirements = extract.extract(lines)
    if not requirements:
        print(
            f"error: no requirements found in {args.rfp.name}. Expected numbered "
            "clauses, 'shall'/'must' statements, or questions. Is this the right "
            "document, or is the requirements schedule a separate file?",
            file=sys.stderr,
        )
        return 3

    chunks, skipped = retrieve.load_corpus(args.corpus)

    # Printed before the results, not after: every skipped document is missing
    # evidence, and the gaps below are overstated until it is resolved.
    if skipped:
        print(f"  WARNING: {len(skipped)} file(s) in the corpus could not be read.")
        print("  Requirements they would have answered will show as gaps.")
        for name, reason in skipped:
            print(f"    - {name}: {reason}")
        print()

    if not chunks:
        print(f"error: no readable documents in {args.corpus}", file=sys.stderr)
        return 4

    index = retrieve.Index(chunks)
    results = report.assess(requirements, index)
    counts = report.tally(results)
    meta = {"rfp": args.rfp.stem, "chunks": len(chunks)}

    report.write_markdown(results, args.out / "coverage.md", meta)
    report.write_csv(results, args.out / "coverage.csv")
    report.write_html(results, args.out / "coverage.html", meta)

    stats = extract.summarise(requirements)
    total = len(results)
    print(f"  RFP          {args.rfp.name}")
    print(f"  Corpus       {len(chunks)} sections from {args.corpus}")
    print(f"  Extracted    {total} requirements "
          f"({stats['binding']} binding, {stats['questions']} questions)")
    print()
    print(f"  ANSWERED     {counts[report.ANSWERED]:>3}  ({counts[report.ANSWERED] / total:.0%})")
    print(f"  WEAK         {counts[report.WEAK]:>3}")
    print(f"  GAP          {counts[report.GAP]:>3}")
    print(f"  CRITICAL     {counts['critical']:>3}  binding, no evidence on file")
    print()
    print(f"  Written to   {args.out}/coverage.{{html,md,csv}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
