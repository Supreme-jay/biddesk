"""Watch a folder: drop a tender in, get a report back.

The people who own bid responses are not the people who run terminals. This
turns the whole tool into a shared folder — drop a tender into `inbox`, the
report appears in `out`, and the tender moves to `done`.

Three things make it survive unattended use:

  * Files are only processed once their size has stopped changing, so a tender
    still copying over the network is never read half-written.
  * One unreadable file moves to `failed/` with the reason beside it; the loop
    keeps running. A watcher that dies on the first bad PDF is useless.
  * The corpus is re-indexed when it changes on disk, so adding a new past
    proposal takes effect without a restart.
"""

from __future__ import annotations

import shutil
import time
import traceback
from pathlib import Path

from . import docparse, extract, report, retrieve

SETTLE_SECONDS = 2.0


def run(corpus_dir: Path, inbox: Path, out_dir: Path, interval: float = 5.0) -> int:
    """Poll `inbox` until interrupted. Returns a process exit code."""
    done_dir = inbox / "done"
    failed_dir = inbox / "failed"
    for directory in (inbox, done_dir, failed_dir, out_dir):
        directory.mkdir(parents=True, exist_ok=True)

    index, chunks, fingerprint = _load_corpus(corpus_dir)
    if index is None:
        print(f"error: no readable documents in {corpus_dir}")
        return 4

    print(f"  Corpus       {len(chunks)} sections from {corpus_dir}")
    print(f"  Watching     {inbox}")
    print(f"  Reports      {out_dir}")
    print(f"  Processed    {done_dir}")
    print("\n  Drop a tender into the watched folder. Ctrl-C to stop.\n")

    pending: dict[Path, int] = {}

    try:
        while True:
            current = _corpus_fingerprint(corpus_dir)
            if current != fingerprint:
                index, chunks, fingerprint = _load_corpus(corpus_dir)
                print(f"  [corpus] reloaded — {len(chunks)} sections\n")

            for path in _candidates(inbox):
                size = path.stat().st_size
                if pending.get(path) != size:
                    # Still growing (or seen for the first time). Wait a cycle.
                    pending[path] = size
                    continue
                pending.pop(path, None)
                _process(path, index, chunks, out_dir, done_dir, failed_dir)

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  Stopped.")
        return 0


def _candidates(inbox: Path) -> list[Path]:
    """Files sitting directly in the inbox, ignoring done/ and failed/."""
    found = []
    for path in sorted(inbox.iterdir()):
        if not path.is_file() or path.name.startswith((".", "~$")):
            continue
        if path.suffix.lower() in docparse.SUPPORTED:
            found.append(path)
    return found


def _process(path: Path, index, chunks, out_dir: Path, done_dir: Path, failed_dir: Path) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"  [{stamp}] {path.name}")

    try:
        # A file can vanish or lock between listing and reading — that is a
        # normal race on a shared folder, not a crash.
        time.sleep(SETTLE_SECONDS)
        raw = docparse.read(path)
        lines = docparse.normalise(raw)
        requirements = extract.extract(lines)

        if not requirements:
            raise ValueError(
                "no requirements found. Expected numbered clauses, 'shall'/'must' "
                "statements, or questions — is the requirements schedule a separate file?"
            )

        results = report.assess(requirements, index)
        counts = report.tally(results)
        meta = {"rfp": path.stem, "chunks": len(chunks)}

        destination = out_dir / _slug(path.stem)
        report.write_html(results, destination / "coverage.html", meta)
        report.write_markdown(results, destination / "coverage.md", meta)
        report.write_csv(results, destination / "coverage.csv")

        print(
            f"           {len(results)} requirements · "
            f"{counts[report.ANSWERED]} answered · {counts[report.WEAK]} weak · "
            f"{counts[report.GAP]} gaps · {counts['critical']} critical"
        )
        print(f"           -> {destination / 'coverage.html'}")
        _move(path, done_dir)

    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the watcher
        reason = str(exc) if str(exc) else type(exc).__name__
        print(f"           FAILED: {reason}")
        moved = _move(path, failed_dir)
        if moved:
            note = moved.with_suffix(moved.suffix + ".error.txt")
            note.write_text(
                f"{path.name} could not be processed.\n\n{reason}\n\n"
                f"---\n{traceback.format_exc()}",
                encoding="utf-8",
            )


def _move(path: Path, destination: Path) -> Path | None:
    """Move a file, disambiguating rather than overwriting a previous run."""
    target = destination / path.name
    if target.exists():
        target = destination / f"{path.stem}-{int(time.time())}{path.suffix}"
    try:
        shutil.move(str(path), str(target))
        return target
    except OSError as exc:
        print(f"           (could not move {path.name}: {exc})")
        return None


def _load_corpus(corpus_dir: Path):
    chunks, skipped = retrieve.load_corpus(corpus_dir)
    for name, reason in skipped:
        print(f"  WARNING: skipping {name}: {reason}")
    if not chunks:
        return None, [], _corpus_fingerprint(corpus_dir)
    return retrieve.Index(chunks), chunks, _corpus_fingerprint(corpus_dir)


def _corpus_fingerprint(corpus_dir: Path) -> tuple:
    """Cheap change detector so a new past proposal takes effect without a restart."""
    try:
        return tuple(sorted(
            (p.name, p.stat().st_mtime_ns, p.stat().st_size)
            for p in corpus_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in docparse.SUPPORTED
        ))
    except OSError:
        return ()


def _slug(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)
    return safe.strip("-").lower() or "report"
