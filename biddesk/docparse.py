"""Document text extraction using only the standard library.

Real RFPs and security questionnaires arrive as .docx and .xlsx far more often
than anything else. Both are ZIP archives of XML, so we can read them without
any third-party dependency -- which keeps the whole tool installable on a
machine with no budget and no pip access.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

# WordprocessingML / SpreadsheetML namespaces
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

SUPPORTED = {".txt", ".md", ".docx", ".xlsx"}


class UnsupportedDocument(Exception):
    """Raised when a file extension has no reader."""


def read(path: Path) -> str:
    """Return the plain text of a document, one logical block per line.

    Every failure surfaces as UnsupportedDocument with a message naming the
    file and what to do about it. Clients routinely send a legacy .doc renamed
    to .docx, a zero-byte download, or a password-protected file, and a raw
    BadZipFile traceback tells them nothing.
    """
    suffix = path.suffix.lower()
    try:
        if suffix in (".txt", ".md"):
            return path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".docx":
            return _read_docx(path)
        if suffix == ".xlsx":
            return _read_xlsx(path)
    except UnsupportedDocument:
        raise
    except zipfile.BadZipFile:
        if path.stat().st_size == 0:
            raise UnsupportedDocument(
                f"{path.name}: file is empty (0 bytes). The download may have failed."
            ) from None
        raise UnsupportedDocument(
            f"{path.name}: not a valid {suffix} file. This is usually a legacy .doc/.xls "
            f"renamed to {suffix}, or a password-protected file. Open it in Office and "
            f"'Save As' a current {suffix}."
        ) from None
    except ET.ParseError as exc:
        raise UnsupportedDocument(
            f"{path.name}: the document XML is damaged ({exc}). Re-save it from Office."
        ) from None
    except (OSError, PermissionError) as exc:
        raise UnsupportedDocument(f"{path.name}: cannot be read ({exc}).") from None

    raise UnsupportedDocument(
        f"{path.name}: no reader for '{suffix}'. Supported: {', '.join(sorted(SUPPORTED))}. "
        "PDFs need conversion to .docx or .txt first."
    )


def _read_docx(path: Path) -> str:
    """Extract paragraphs and table cells from a Word document.

    Table cells matter: questionnaire-style RFPs put one requirement per row,
    and dropping tables would lose most of the document.
    """
    with zipfile.ZipFile(path) as archive:
        try:
            xml = archive.read("word/document.xml")
        except KeyError as exc:
            raise UnsupportedDocument(f"{path.name}: not a Word document") from exc

    root = ET.fromstring(xml)
    body = root.find(f"{_W}body")
    if body is None:
        body = root

    lines: list[str] = []
    for child in body:
        if child.tag == f"{_W}p":
            text = _para_text(child)
            if text:
                lines.append(text)
        elif child.tag == f"{_W}tbl":
            lines.extend(_table_rows(child))

    if not lines:
        # Content controls and some generators nest paragraphs below the body,
        # so fall back to a flat sweep rather than returning nothing.
        lines = [t for p in root.iter(f"{_W}p") if (t := _para_text(p))]

    return "\n".join(lines)


def _para_text(para: ET.Element) -> str:
    """Join every run in a paragraph.

    Word splits a sentence across runs at any formatting change, so reading
    only the first <w:t> silently truncates text mid-sentence.
    """
    return "".join(node.text or "" for node in para.iter(f"{_W}t")).strip()


def _table_rows(table: ET.Element) -> list[str]:
    """Flatten a table to one line per row, cells joined by ' | '.

    Questionnaire-style tenders put the clause reference in one cell and the
    requirement in the next. Emitting cells as separate lines would orphan
    every reference from the text it belongs to.
    """
    rows = []
    for row in table.iter(f"{_W}tr"):
        cells = []
        for cell in row.iter(f"{_W}tc"):
            text = " ".join(
                t for p in cell.iter(f"{_W}p") if (t := _para_text(p))
            ).strip()
            if text:
                cells.append(text)
        if cells:
            rows.append(" | ".join(cells))
    return rows


def _read_xlsx(path: Path) -> str:
    """Flatten a spreadsheet to one line per row, cells joined by ' | '.

    Security questionnaires are overwhelmingly xlsx with one question per row,
    so preserving row structure keeps each requirement intact.
    """
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        sheets = sorted(
            n for n in archive.namelist()
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        )
        if not sheets:
            raise UnsupportedDocument(f"{path.name}: no worksheets found")

        lines: list[str] = []
        for sheet in sheets:
            root = ET.fromstring(archive.read(sheet))
            for row in root.iter(f"{_S}row"):
                cells = [_cell_text(c, shared) for c in row.iter(f"{_S}c")]
                cells = [c for c in cells if c]
                if cells:
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """xlsx stores repeated strings in a shared table referenced by index."""
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(t.text or "" for t in si.iter(f"{_S}t")).strip()
        for si in root.iter(f"{_S}si")
    ]


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    value = cell.find(f"{_S}v")
    if cell.get("t") == "s":  # shared-string index
        if value is None or not value.text:
            return ""
        try:
            return shared[int(value.text)]
        except (ValueError, IndexError):
            return ""
    if cell.get("t") == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_S}t")).strip()
    return (value.text or "").strip() if value is not None else ""


def normalise(text: str) -> list[str]:
    """Split raw text into clean, non-empty lines with collapsed whitespace."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            out.append(line)
    return out
