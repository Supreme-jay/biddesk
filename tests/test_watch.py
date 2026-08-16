"""End-to-end test of folder-watch mode.

Run: python tests/test_watch.py

Watch mode runs unattended on someone else's machine, so the properties that
matter are the ones nobody is present to notice: a bad file must not stop the
loop, and a processed file must not be silently lost.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

failures: list[str] = []

CORPUS = """\
# Service Desk
Our service desk operates 24 hours a day, 365 days a year from Manchester.
Priority 1 incidents receive a response within 15 minutes and resolution within 4 hours.

# Security
We hold ISO 27001 certification and encrypt all data at rest using AES-256.
Multi-factor authentication is enforced for all privileged and remote access.
"""

TENDER = """\
3.1.1 The Supplier shall operate a service desk available 24 hours per day.
3.1.2 The Supplier shall respond to Priority 1 incidents within 15 minutes.
4.1.1 The Supplier shall hold ISO 27001 certification.
6.1.1 The Supplier shall submit a Carbon Reduction Plan for Net Zero by 2050.
"""


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")
        failures.append(label)


def wait_for(predicate, timeout: float = 45.0, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        corpus, inbox, out = root / "corpus", root / "inbox", root / "out"
        corpus.mkdir()
        inbox.mkdir()
        (corpus / "past-proposal.md").write_text(CORPUS, encoding="utf-8")

        process = subprocess.Popen(
            [sys.executable, "-m", "biddesk", "watch",
             "--corpus", str(corpus), "--inbox", str(inbox),
             "--out", str(out), "--interval", "1"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

        try:
            print("startup")
            check("watcher stays running", wait_for(lambda: (inbox / "done").is_dir()), True)

            print("\nprocessing a tender")
            (inbox / "tender-one.md").write_text(TENDER, encoding="utf-8")
            report = out / "tender-one" / "coverage.html"
            check("report produced", wait_for(lambda: report.is_file()), True)
            check("csv produced", (out / "tender-one" / "coverage.csv").is_file(), True)
            check("source moved to done/",
                  wait_for(lambda: (inbox / "done" / "tender-one.md").is_file()), True)
            check("inbox cleared", (inbox / "tender-one.md").exists(), False)

            body = report.read_text(encoding="utf-8")
            check("gap detected in report", "Carbon Reduction Plan" in body, True)
            check("report is self-contained", "<style>" in body, True)

            print("\na damaged file must not stop the loop")
            (inbox / "broken.docx").write_bytes(b"\xd0\xcf\x11\xe0not a zip at all")
            check("bad file moved to failed/",
                  wait_for(lambda: (inbox / "failed" / "broken.docx").is_file()), True)
            note = inbox / "failed" / "broken.docx.error.txt"
            check("reason written beside it", wait_for(lambda: note.is_file()), True)
            check("reason is actionable",
                  "Save As" in note.read_text(encoding="utf-8"), True)

            print("\nstill alive afterwards")
            (inbox / "tender-two.md").write_text(TENDER, encoding="utf-8")
            check("keeps processing after a failure",
                  wait_for(lambda: (out / "tender-two" / "coverage.html").is_file()), True)

            print("\nunsupported extensions are ignored, not failed")
            (inbox / "notes.rtf").write_text("ignore me", encoding="utf-8")
            time.sleep(3)
            check("unsupported file left alone", (inbox / "notes.rtf").is_file(), True)
            check("not moved to failed/", (inbox / "failed" / "notes.rtf").exists(), False)

            check("process still running", process.poll(), None)

        finally:
            process.terminate()
            try:
                output = process.communicate(timeout=10)[0]
            except subprocess.TimeoutExpired:
                process.kill()
                output = process.communicate()[0]

        if failures:
            print("\n--- watcher output ---")
            print(output)

    print()
    if failures:
        print(f"  FAILED ({len(failures)}): {failures}")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
