"""Round-trip the Office readers against synthetic .docx / .xlsx files.

Real RFPs arrive as Word and Excel far more often than plain text, so these
readers carry the tool. They are written against the raw OOXML with no
third-party library, which makes them exactly the code most likely to be
subtly wrong and least likely to announce it.

Run: python tests/test_docparse.py
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from biddesk import docparse, extract  # noqa: E402

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

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
        print(f"  FAIL  {label}\n          {needle!r} not found in {haystack[:160]!r}")
        failures.append(label)


def build_docx(path: Path) -> None:
    """A Word document with body paragraphs and a two-row requirements table."""
    def para(text: str) -> str:
        return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"

    def cell(text: str) -> str:
        return f"<w:tc>{para(text)}</w:tc>"

    body = (
        para("Section 4 - Security Requirements")
        + para("4.1.1 The Supplier shall hold ISO 27001 certification.")
        # A run split across formatting boundaries: Word does this constantly
        # and a naive reader concatenating only the first <w:t> loses text.
        + '<w:p><w:r><w:t xml:space="preserve">4.1.2 The Supplier must </w:t></w:r>'
          '<w:r><w:t>encrypt all data at rest.</w:t></w:r></w:p>'
        + "<w:tbl>"
        + f"<w:tr>{cell('Ref')}{cell('Requirement')}</w:tr>"
        + f"<w:tr>{cell('4.1.3')}{cell('The Supplier shall provide 24/7 service desk cover.')}</w:tr>"
        + "</w:tbl>"
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)


def build_xlsx(path: Path) -> None:
    """A questionnaire sheet: shared strings, an inline string, and a number."""
    shared = [
        "Question", "Answer",
        "Do you hold Cyber Essentials Plus certification?",
        "Describe your incident response process.",
    ]
    si = "".join(f"<si><t>{s}</t></si>" for s in shared)
    shared_xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<sst xmlns="{S}" count="{len(shared)}" uniqueCount="{len(shared)}">{si}</sst>'
    )
    sheet_xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{S}"><sheetData>'
        f'<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        f'<row r="2"><c r="A2" t="s"><v>2</v></c>'
        f'<c r="B2" t="inlineStr"><is><t>Yes - renewed annually</t></is></c></row>'
        f'<row r="3"><c r="A3" t="s"><v>3</v></c><c r="B3"><v>42</v></c></row>'
        f'</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        print("docx")
        docx = root / "tender.docx"
        build_docx(docx)
        text = docparse.read(docx)
        lines = docparse.normalise(text)
        check_in("paragraph text read", "4.1.1 The Supplier shall hold ISO 27001", text)
        check_in("split runs joined", "4.1.2 The Supplier must encrypt all data at rest.", text)
        check_in("table cell text read", "24/7 service desk cover", text)
        reqs = extract.extract(lines)
        refs = {r.ref for r in reqs}
        check("clause refs extracted", refs, {"4.1.1", "4.1.2", "4.1.3"})
        check("all marked binding", all(r.binding for r in reqs), True)

        print("\nxlsx")
        xlsx = root / "questionnaire.xlsx"
        build_xlsx(xlsx)
        text = docparse.read(xlsx)
        lines = docparse.normalise(text)
        check_in("shared string resolved", "Cyber Essentials Plus", text)
        check_in("inline string resolved", "Yes - renewed annually", text)
        check_in("numeric cell resolved", "42", text)
        check_in("row structure preserved", " | ", text)
        reqs = extract.extract(lines)
        texts = [r.text for r in reqs]
        check("both questions extracted", len(reqs), 2)
        check("header row rejected", any("Question | Answer" in t for t in texts), False)

        print("\nerrors")
        # .rtf has no reader. (.pdf does now -- see tests/test_pdf.py.)
        bad = root / "spec.rtf"
        bad.write_text("{" + chr(92) + "rtf1 hello}", encoding="utf-8")
        try:
            docparse.read(bad)
            check("unsupported extension raises", False, True)
        except docparse.UnsupportedDocument as exc:
            check_in("unsupported names the file", "spec.rtf", str(exc))
            check_in("unsupported lists what is supported", ".docx", str(exc))

    print()
    if failures:
        print(f"  FAILED ({len(failures)}): {failures}")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
