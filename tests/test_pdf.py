"""Round-trip the stdlib PDF reader against synthetic PDFs.

Run: python tests/test_pdf.py

The reader parses raw PDF operators with no third-party library, which makes it
the code most likely to be subtly wrong. The tests that matter most are the
refusals: a scanned or badly-encoded PDF must fail loudly, because unreadable
text still produces "requirements" that would be scored and reported as gaps.
"""

from __future__ import annotations

import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from biddesk import docparse, extract, pdfread  # noqa: E402

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
        print(f"  FAIL  {label}\n          {needle!r} not in {haystack[:200]!r}")
        failures.append(label)


def check_raises(label: str, data: bytes, *needles: str) -> None:
    try:
        pdfread.extract(data)
    except pdfread.NotExtractable as exc:
        missing = [n for n in needles if n.lower() not in str(exc).lower()]
        if missing:
            print(f"  FAIL  {label}\n          message lacks {missing}: {exc}")
            failures.append(label)
        else:
            print(f"  ok    {label}")
    except Exception as exc:  # noqa: BLE001 - catching leaks is the point
        print(f"  FAIL  {label}\n          leaked {type(exc).__name__}: {exc}")
        failures.append(label)
    else:
        print(f"  FAIL  {label}\n          no error raised")
        failures.append(label)


def escape(text: str) -> str:
    for old, new in ((chr(92), chr(92) * 2), ("(", chr(92) + "("), (")", chr(92) + ")")):
        text = text.replace(old, new)
    return text


def build_pdf(lines: list[str], compress: bool = True, encrypted: bool = False) -> bytes:
    """A single-page PDF whose content stream shows each line via Tj."""
    ops, y = [], 720
    for line in lines:
        ops.append(f"BT /F1 12 Tf 72 {y} Td ({escape(line)}) Tj ET")
        y -= 20
    content = "\n".join(ops).encode("latin-1")
    filt = b""
    if compress:
        content = zlib.compress(content)
        filt = b"/Filter/FlateDecode"

    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/Contents 4 0 R>>",
        b"<</Length " + str(len(content)).encode() + filt + b">>\nstream\n"
        + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    for number, body in enumerate(objects, start=1):
        out += str(number).encode() + b" 0 obj " + body + b" endobj\n"
    trailer = b"<</Encrypt 9 0 R/Root 1 0 R>>" if encrypted else b"<</Root 1 0 R>>"
    out += b"trailer " + trailer + b"\n%%EOF\n"
    return bytes(out)


def build_hex_pdf(text: str) -> bytes:
    """A PDF using a UTF-16BE hex string, as CID-font documents do."""
    payload = text.encode("utf-16-be").hex().upper()
    content = zlib.compress(f"BT /F1 12 Tf 72 700 Td <{payload}> Tj ET".encode("latin-1"))
    body = (b"<</Length " + str(len(content)).encode() + b"/Filter/FlateDecode>>\nstream\n"
            + content + b"\nendstream")
    return (b"%PDF-1.4\n1 0 obj " + body + b" endobj\ntrailer <</Root 1 0 R>>\n%%EOF\n")


REQUIREMENTS = [
    "3.1.1 The Supplier shall hold ISO 27001 certification.",
    "4.1.2 The Supplier must encrypt all data at rest (AES-256).",
    "5.1.1 Describe your incident response process.",
]


def main() -> int:
    print("text extraction")
    for label, compress in (("flate-compressed", True), ("uncompressed", False)):
        text = pdfread.extract(build_pdf(REQUIREMENTS, compress=compress))
        check_in(f"{label}: clause read", "shall hold ISO 27001 certification", text)
        check(f"{label}: not flagged garbled", pdfread.looks_garbled(text), False)

    text = pdfread.extract(build_pdf(REQUIREMENTS))
    check_in("parenthesised text survives", "(AES-256)", text)
    check_in("line breaks preserved", "\n", text)

    reqs = extract.extract(docparse.normalise(text))
    check("all three requirements extracted", len(reqs), 3)
    check("clause refs parsed", {r.ref for r in reqs}, {"3.1.1", "4.1.2", "5.1.1"})
    check("imperative ask classified",
          next(r.kind for r in reqs if r.ref == "5.1.1"), "question")

    print("\nstring edge cases")
    # Round-trip is the real property: whatever went in comes back verbatim,
    # including literal backslashes, which must survive PDF's own escaping.
    tricky = [
        "6.1.1 Nested (parentheses (inside)) must survive.",
        "6.1.2 Escaped " + chr(92) + "(paren" + chr(92) + ") and backslash.",
    ]
    text = pdfread.extract(build_pdf(tricky))
    check("tricky strings round-trip verbatim",
          [line for line in text.splitlines() if line.strip()], tricky)

    text = pdfread.extract(build_hex_pdf("4.1.7 Data shall remain in the UK."))
    check_in("utf-16be hex string decoded", "Data shall remain in the UK", text)

    print("\nrefusals")
    check_raises("encrypted pdf", build_pdf(REQUIREMENTS, encrypted=True),
                 "password-protected", "docx")
    scanned = (b"%PDF-1.4\n1 0 obj <</Length 30>>\nstream\n"
               + zlib.compress(b"q 612 0 0 792 0 0 cm /Im1 Do Q")
               + b"\nendstream endobj\ntrailer <</Root 1 0 R>>\n%%EOF")
    check_raises("scanned pdf (no text operators)", scanned, "scanned", "ocr")
    check_raises("not a pdf at all", b"just some bytes", "not a PDF")

    print("\ngarbled detection")
    check("mojibake flagged", pdfread.looks_garbled("��\x01\x02" * 40), True)
    check("short output flagged", pdfread.looks_garbled("abc"), True)
    check("real prose not flagged",
          pdfread.looks_garbled("The Supplier shall maintain certification " * 4), False)

    print("\nvia docparse")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "tender.pdf"
        good.write_bytes(build_pdf(REQUIREMENTS))
        check_in("docparse reads pdf", "ISO 27001", docparse.read(good))
        check("pdf is a supported format", ".pdf" in docparse.SUPPORTED, True)

        scanned_path = Path(tmp) / "scan.pdf"
        scanned_path.write_bytes(scanned)
        try:
            docparse.read(scanned_path)
            check("docparse surfaces scanned pdf", False, True)
        except docparse.UnsupportedDocument as exc:
            check_in("scanned pdf names the file", "scan.pdf", str(exc))

    print()
    if failures:
        print(f"  FAILED ({len(failures)}): {failures}")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
