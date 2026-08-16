"""Failure-path tests: the files a real client actually sends.

Clients send legacy .doc files renamed to .docx, zero-byte downloads,
password-protected documents, and PDFs. None of these should produce a
traceback, and none should be dropped from the corpus in silence.

Run: python tests/test_robustness.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from biddesk import docparse, extract, retrieve  # noqa: E402

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")
        failures.append(label)


def check_raises(label: str, path: Path, *needles: str) -> None:
    """The reader must fail cleanly and say something actionable."""
    try:
        docparse.read(path)
    except docparse.UnsupportedDocument as exc:
        message = str(exc).lower()
        missing = [n for n in needles if n.lower() not in message]
        if missing:
            print(f"  FAIL  {label}\n          message lacks {missing}: {exc}")
            failures.append(label)
        else:
            print(f"  ok    {label}")
    except Exception as exc:  # noqa: BLE001 - the whole point is catching leaks
        print(f"  FAIL  {label}\n          leaked {type(exc).__name__}: {exc}")
        failures.append(label)
    else:
        print(f"  FAIL  {label}\n          no error raised")
        failures.append(label)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        print("damaged documents")
        legacy = root / "tender.docx"
        legacy.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy OLE2 payload")
        check_raises("legacy .doc renamed .docx", legacy, "tender.docx", "save as")

        empty = root / "download.docx"
        empty.write_bytes(b"")
        check_raises("zero-byte file", empty, "empty", "download")

        broken = root / "broken.docx"
        with zipfile.ZipFile(broken, "w") as archive:
            archive.writestr("word/document.xml", "<w:document><UNCLOSED>")
        check_raises("damaged document XML", broken, "damaged")

        notdocx = root / "archive.docx"
        with zipfile.ZipFile(notdocx, "w") as archive:
            archive.writestr("readme.txt", "not a word file")
        check_raises("zip without document.xml", notdocx, "word document")

        badxlsx = root / "sheet.xlsx"
        badxlsx.write_bytes(b"this is plainly not a zip")
        check_raises("corrupt xlsx", badxlsx, "sheet.xlsx", "save as")

        # PDFs are read now (tests/test_pdf.py); .rtf still has no reader.
        rtf = root / "spec.rtf"
        rtf.write_text("{" + chr(92) + "rtf1 hello}", encoding="utf-8")
        check_raises("unsupported format rejected with guidance", rtf, "no reader", ".docx")

        textless = root / "scan.pdf"
        textless.write_bytes(b"%PDF-1.7\ntrailer<</Root 1 0 R>>\n%%EOF")
        check_raises("pdf with no text rejected", textless, "scan.pdf", "scanned")

        print("\ncorpus loading")
        corpus = root / "corpus"
        corpus.mkdir()
        (corpus / "good.md").write_text(
            "# Security\nWe hold ISO 27001 certification and encrypt data at rest "
            "using AES-256 across all managed environments.",
            encoding="utf-8",
        )
        (corpus / "ruined.docx").write_bytes(b"\xd0\xcf\x11\xe0not a zip")
        (corpus / "blank.md").write_text("   \n\n  \n", encoding="utf-8")
        (corpus / "notes.rtf").write_text("{" + chr(92) + "rtf1}", encoding="utf-8")
        (corpus / "scan.pdf").write_bytes(b"%PDF-1.7\ntrailer<</Root 1 0 R>>\n%%EOF")

        chunks, skipped = retrieve.load_corpus(corpus)
        names = {name for name, _ in skipped}
        check("good file still indexed", len(chunks) >= 1, True)
        check("all four bad files reported", names,
              {"ruined.docx", "blank.md", "notes.rtf", "scan.pdf"})
        reasons = dict(skipped)
        check("blank file reason is specific", reasons["blank.md"], "no readable text found")
        check("unsupported reason names format",
              "unsupported format" in reasons["notes.rtf"], True)
        check("unreadable pdf reason says scanned",
              "scanned" in reasons["scan.pdf"].lower(), True)

        empty_dir = root / "nothing"
        empty_dir.mkdir()
        chunks, skipped = retrieve.load_corpus(empty_dir)
        check("empty corpus returns empty", (chunks, skipped), ([], []))

        print("\nextraction edge cases")
        check("empty input", extract.extract([]), [])
        check("whitespace only", extract.extract(["   ", ""]), [])
        check("prose without obligation ignored",
              extract.extract(["This document describes the background to the project."]), [])
        check("page furniture ignored",
              extract.extract(["Page 4 of 22", "3. Requirements .......... 14"]), [])
        dupes = extract.extract([
            "The Supplier shall hold ISO 27001 certification.",
            "The Supplier shall hold ISO 27001 certification.",
        ])
        check("duplicates collapsed", len(dupes), 1)

        print("\nscoring edge cases")
        index = retrieve.Index([])
        check("empty index scores zero", index.evaluate(["iso", "27001"]), (None, [], 0.0, 0.0))
        chunks, _ = retrieve.load_corpus(corpus)
        index = retrieve.Index(chunks)
        check("empty query scores zero", index.evaluate([]), (None, [], 0.0, 0.0))
        best, _, cov, _ = index.evaluate(retrieve.tokenise("Describe your Welsh language provision."))
        check("unrelated query scores low", cov < 0.42, True)

        print("\ncli")
        rfp = root / "rfp.md"
        rfp.write_text("3.1.1 The Supplier shall hold ISO 27001 certification.\n",
                       encoding="utf-8")

        result = _cli(["--rfp", str(rfp), "--corpus", str(corpus), "--out", str(root / "o")])
        check("happy path exits 0", result.returncode, 0)
        check("skipped files warned about", "WARNING" in result.stdout, True)
        check("report written", (root / "o" / "coverage.html").is_file(), True)

        blocker = root / "afile.txt"
        blocker.write_text("x", encoding="utf-8")
        result = _cli(["--rfp", str(rfp), "--corpus", str(corpus), "--out", str(blocker)])
        check("--out as file rejected", result.returncode, 2)

        prose = root / "prose.md"
        prose.write_text("This document sets out the background to the procurement.\n",
                         encoding="utf-8")
        result = _cli(["--rfp", str(prose), "--corpus", str(corpus), "--out", str(root / "o2")])
        check("no requirements exits 3", result.returncode, 3)
        check("no-requirements message is useful",
              "numbered clauses" in result.stderr, True)

        result = _cli(["--rfp", str(rfp), "--corpus", str(corpus), "--out", str(root / "o3"),
                       "--weak-at", "0.8", "--answered-at", "0.3"])
        check("inverted thresholds rejected", result.returncode, 2)

        result = _cli(["--rfp", str(rfp), "--corpus", str(root / "missing"),
                       "--out", str(root / "o4")])
        check("missing corpus rejected", result.returncode, 2)

    print()
    if failures:
        print(f"  FAILED ({len(failures)}): {failures}")
        return 1
    print("  PASS")
    return 0


def _cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "biddesk", *args],
        cwd=ROOT, capture_output=True, text=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
