"""Command line entry point.

    python -m grantdesk run   --call <file> --library <dir>    score a grant call
    python -m grantdesk draft --call <file> --library <dir>    score and draft
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from biddesk import docparse

from . import extract, score as scoring

COMMANDS = ("run", "draft")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = _build_parser()
    if not argv:
        parser.print_help()
        return 2

    if argv[0] not in COMMANDS and argv[0] not in ("-h", "--help"):
        argv = ["run", *argv]

    args = parser.parse_args(argv)

    if not args.library.is_dir():
        parser.error(f"Library directory not found: {args.library}")
    if not args.call.is_file():
        parser.error(f"Grant call not found: {args.call}")
    if args.out.exists() and not args.out.is_dir():
        parser.error(f"--out must be a directory, but {args.out} is a file")

    scored = _score(args, parser)
    if isinstance(scored, int):
        return scored

    if args.command == "draft":
        return _draft(args, scored)
    return 0


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grantdesk",
        description="Draft a grant application from a funding call and your past applications.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="{run,draft}")

    def shared(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--library", required=True, type=Path,
                         help="Directory of past applications and supporting documents")
        sub.add_argument("--out", type=Path, default=Path("out"),
                         help="Output directory (default: ./out)")

    run = subparsers.add_parser("run", help="Analyse a grant call and show what you can reuse")
    run.add_argument("--call", required=True, type=Path,
                     help="Grant call / funding brief (.pdf .docx .xlsx .md .txt)")
    shared(run)

    draft = subparsers.add_parser(
        "draft", help="Analyse and draft every section (needs an API key)")
    draft.add_argument("--call", required=True, type=Path, help="Grant call / funding brief")
    draft.add_argument("--effort", default="high",
                       choices=["low", "medium", "high", "xhigh", "max"],
                       help="Model effort (default: high)")
    draft.add_argument("--batch-size", type=int, default=5,
                       help="Sections per API call (default: 5)")
    draft.add_argument("--dry-run", action="store_true",
                       help="Print the prompts that would be sent. Costs nothing")
    shared(draft)

    return parser


def _score(args, parser):
    from biddesk.retrieve import load_corpus, Index

    try:
        raw = docparse.read(args.call)
    except docparse.UnsupportedDocument as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    lines = docparse.normalise(raw)
    if not lines:
        print(f"error: {args.call.name} contains no readable text.", file=sys.stderr)
        return 3

    sections = extract.extract(lines)
    if not sections:
        print(
            f"error: no grant sections found in {args.call.name}. Expected questions, "
            "'describe'/'explain' prompts, or numbered sections.",
            file=sys.stderr,
        )
        return 3

    chunks, skipped = load_corpus(args.library)

    if skipped:
        print(f"  WARNING: {len(skipped)} file(s) in the library could not be read.")
        for name, reason in skipped:
            print(f"    - {name}: {reason}")
        print()

    if not chunks:
        print(f"error: no readable documents in {args.library}", file=sys.stderr)
        return 4

    index = Index(chunks)
    results = scoring.score_all(sections, index)

    evidenced = sum(1 for r in results if r.source_tag == "EVIDENCED")
    generated = sum(1 for r in results if r.source_tag == "GENERATED")
    stats = extract.summarise(sections)

    print(f"  Grant call   {args.call.name}")
    print(f"  Library      {len(chunks)} sections from {args.library}")
    print(f"  Extracted    {stats['total']} sections "
          f"({stats['narrative']} narrative, {stats['questions']} questions)")
    if stats['with_word_limit']:
        print(f"  Word limits  {stats['with_word_limit']} sections have limits")
    print()
    print(f"  EVIDENCED    {evidenced:>3}  past material available")
    print(f"  GENERATED    {generated:>3}  will need AI draft + human review")
    print()

    # Write analysis report even in run mode
    from . import render
    render.write_analysis_md(results, args.out / "analysis.md",
                             {"call": args.call.stem, "chunks": len(chunks)})

    print(f"  Written to   {args.out}/analysis.md")
    return results


def _draft(args, results) -> int:
    from . import draft as drafting, render

    print(f"\n  Drafting     {len(results)} sections")

    if args.dry_run:
        prompts = drafting.preview(results, args.batch_size)
        for number, (kind, ids, prompt) in enumerate(prompts, start=1):
            print(f"\n===== batch {number}/{len(prompts)} ({kind}): {', '.join(ids)} =====")
            print(prompt)
        print("\n  Dry run — nothing was sent.")
        return 0

    def progress(done, total, ids, kind):
        print(f"    batch {done}/{total} ({kind}): {', '.join(ids)}")

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

    render.write_markdown(drafts, results, args.out / "application.md",
                          {"call": args.call.stem})
    render.write_csv(drafts, results, args.out / "sections.csv")
    render.write_html(drafts, results, args.out / "application.html",
                      {"call": args.call.stem})

    evidenced = sum(1 for d in drafts if d.source_tag == "EVIDENCED")
    generated = sum(1 for d in drafts if d.source_tag == "GENERATED")
    print(f"\n  From past material  {evidenced}")
    print(f"  AI-generated        {generated}  (review needed)")
    print(f"  Written to          {args.out}/application.{{html,md,csv}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
