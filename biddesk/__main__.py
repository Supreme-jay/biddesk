"""Command line entry point.

    python -m biddesk run   --rfp <file> --corpus <dir>     score one tender
    python -m biddesk watch --inbox <dir> --corpus <dir>    watch a folder
    python -m biddesk draft --rfp <file> --corpus <dir>     score, then draft

`run` is the default, so the original flag-only form still works:

    python -m biddesk --rfp <file> --corpus <dir>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import docparse, extract, report, retrieve

COMMANDS = ("run", "watch", "draft")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = _build_parser()
    if not argv:
        parser.print_help()
        return 2
    # Bare flags keep working: treat them as arguments to `run`.
    if argv[0] not in COMMANDS and argv[0] not in ("-h", "--help"):
        argv = ["run", *argv]

    args = parser.parse_args(argv)

    if not args.corpus.is_dir():
        parser.error(f"Corpus directory not found: {args.corpus}")
    if args.out.exists() and not args.out.is_dir():
        parser.error(f"--out must be a directory, but {args.out} is a file")
    if not 0.0 < args.weak_at < args.answered_at <= 1.0:
        parser.error("thresholds must satisfy 0 < --weak-at < --answered-at <= 1")

    report.ANSWERED_AT = args.answered_at
    report.WEAK_AT = args.weak_at

    if args.command == "watch":
        from . import watch

        return watch.run(args.corpus, args.inbox, args.out, args.interval)

    if not args.rfp.is_file():
        parser.error(f"RFP not found: {args.rfp}")

    scored = _score(args, parser)
    if isinstance(scored, int):
        return scored
    results, chunks = scored

    if args.command == "draft":
        return _draft(args, results)
    return 0


def _force_utf8_console() -> None:
    """Make stdout/stderr safe for non-ASCII.

    The Windows console defaults to cp1252, so printing an arrow or an em-dash
    raises UnicodeEncodeError and takes the whole run down — after the work is
    already done. Report text legitimately contains both.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="biddesk",
        description="Score an incoming tender against your library of past proposals.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="{run,watch,draft}")

    def shared(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--corpus", required=True, type=Path,
                         help="Directory of past proposals and capability statements")
        sub.add_argument("--out", type=Path, default=Path("out"),
                         help="Output directory (default: ./out)")
        sub.add_argument("--answered-at", type=float, default=report.ANSWERED_AT,
                         help=f"Coverage at or above which a requirement counts as "
                              f"answered (default {report.ANSWERED_AT})")
        sub.add_argument("--weak-at", type=float, default=report.WEAK_AT,
                         help=f"Coverage below which a requirement is a gap "
                              f"(default {report.WEAK_AT})")

    run = subparsers.add_parser("run", help="Score a single tender")
    run.add_argument("--rfp", required=True, type=Path,
                     help="Incoming tender / questionnaire (.pdf .docx .xlsx .md .txt)")
    shared(run)

    watch = subparsers.add_parser("watch", help="Watch a folder and report on what lands in it")
    watch.add_argument("--inbox", required=True, type=Path,
                       help="Folder to watch. Tenders dropped here are processed automatically")
    watch.add_argument("--interval", type=float, default=5.0,
                       help="Seconds between checks (default: 5)")
    shared(watch)

    draft = subparsers.add_parser(
        "draft", help="Score, then draft answers where evidence exists (needs an API key)")
    draft.add_argument("--rfp", required=True, type=Path, help="Incoming tender")
    draft.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"],
                       help="Model effort (default: high)")
    draft.add_argument("--batch-size", type=int, default=5,
                       help="Requirements per API call (default: 5)")
    draft.add_argument("--dry-run", action="store_true",
                       help="Print the prompts that would be sent and stop. Costs nothing")
    shared(draft)

    return parser


def _score(args, parser):
    """Extract, score, and write the coverage reports. Returns (results, chunks)."""
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
    return results, chunks


def _draft(args, results) -> int:
    from . import draft as drafting

    targets = drafting.draftable(results)
    if not targets:
        print("\n  Nothing to draft: no requirement has supporting evidence.")
        return 0

    print(f"\n  Drafting     {len(targets)} requirements with evidence "
          f"({len(results) - len(targets)} gaps skipped — nothing to ground an answer in)")

    if args.dry_run:
        prompts = drafting.preview(results, args.batch_size)
        for number, (ids, prompt) in enumerate(prompts, start=1):
            print(f"\n===== batch {number}/{len(prompts)}: {', '.join(ids)} =====")
            print(prompt)
        print("\n  Dry run — nothing was sent and nothing was charged.")
        return 0

    def progress(done: int, total: int, ids: list[str]) -> None:
        print(f"    batch {done}/{total}: {', '.join(ids)}")

    try:
        drafts = drafting.generate(
            results,
            effort=args.effort,
            batch_size=args.batch_size,
            progress=progress,
        )
    except drafting.DraftingUnavailable as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 5

    path = args.out / "drafts.md"
    drafting.write_markdown(drafts, results, path)
    refused = sum(1 for d in drafts if not d.evidence_sufficient)
    print(f"\n  Drafted      {len(drafts) - refused}")
    print(f"  Refused      {refused}  evidence too thin — left for a human")
    print(f"  Written to   {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
