"""Extract text from digitally-generated PDFs using only the standard library.

Tenders arrive as PDF constantly, and requiring a conversion step kills a sales
demo. A PDF's page content is a Flate-compressed stream of drawing operators,
and `zlib` is in the standard library -- so the common case (a PDF exported
from Word or a similar tool) is reachable without adding a dependency to the
core tool.

What this does NOT do, and says so loudly rather than returning plausible
nonsense:

  * Scanned/image PDFs. There is no text to extract, only pixels. Raises.
  * Encrypted PDFs. Raises.
  * Exotic font encodings with no usable mapping. Extraction returns mojibake,
    which `looks_garbled` detects so the caller can refuse the file.

The failure mode that matters is a PDF that extracts *badly* rather than not at
all: garbled text still produces requirements, and those requirements would be
scored against the proposal library and reported as gaps. Silence would be a
confidently wrong report, so quality is checked, not assumed.
"""

from __future__ import annotations

import re
import zlib

# A content stream's payload sits between `stream` and `endstream`.
_STREAM = re.compile(rb"stream\r?\n")

# Operators worth tracking. Text-showing ops emit content; positioning ops are
# where a line break belongs.
_OPERATOR = re.compile(rb"(?:BT|ET|Tj|TJ|T\*|Td|TD|Tm|'|\")(?![A-Za-z0-9])")
_BREAKS = {b"Td", b"TD", b"T*", b"'", b'"', b"BT", b"ET"}

_ESCAPES = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}


class NotExtractable(Exception):
    """The PDF holds no recoverable text (scanned, encrypted, or malformed)."""


def extract(data: bytes) -> str:
    """Return the text of a PDF, one line per text-positioning break."""
    if not data.startswith(b"%PDF"):
        raise NotExtractable("not a PDF file (missing %PDF header)")

    if _is_encrypted(data):
        raise NotExtractable(
            "the PDF is password-protected or encrypted. Remove the protection "
            "and re-save it, or export it to .docx"
        )

    chunks: list[str] = []
    for payload in _streams(data):
        content = _inflate(payload)
        if content is None or not _is_content_stream(content):
            continue
        text = _text_from_content(content)
        if text.strip():
            chunks.append(text)

    if not chunks:
        raise NotExtractable(
            "no extractable text found. This is usually a scanned PDF (page images "
            "rather than text), which needs OCR first. If it opens in a PDF reader "
            "and you cannot select the text with the cursor, it is scanned"
        )

    return "\n".join(chunks)


def looks_garbled(text: str) -> bool:
    """True when extraction produced bytes that are not plausibly prose.

    A PDF using a custom font encoding without a usable mapping extracts to
    consistent nonsense. It is better to refuse the file than to score
    gibberish against the proposal library and report the result as gaps.
    """
    sample = text[:4000]
    if len(sample) < 40:
        return True
    readable = sum(1 for ch in sample if ch.isalpha() or ch.isspace() or ch in ".,;:()/-")
    return (readable / len(sample)) < 0.75


def _is_encrypted(data: bytes) -> bool:
    # /Encrypt in the trailer means the document streams are encrypted. Checking
    # only the tail avoids matching the token inside an unrelated content stream.
    return b"/Encrypt" in data[-3000:] or b"/Encrypt" in data[:1500]


def _streams(data: bytes):
    for match in _STREAM.finditer(data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end != -1:
            yield data[start:end]


def _inflate(payload: bytes) -> bytes | None:
    """Decompress a stream, or return it unchanged when it is already plain."""
    for window in (15, -15):
        try:
            return zlib.decompress(payload, window)
        except zlib.error:
            continue
    # Uncompressed content streams are legal and appear in simpler generators.
    return payload if _is_content_stream(payload) else None


def _is_content_stream(content: bytes) -> bool:
    return b"BT" in content and (b"Tj" in content or b"TJ" in content)


def _text_from_content(content: bytes) -> str:
    """Walk a content stream, collecting shown strings and line breaks."""
    parts: list[str] = []
    index, length = 0, len(content)

    while index < length:
        char = content[index : index + 1]

        if char == b"(":
            raw, index = _literal(content, index)
            parts.append(_decode(raw))
            continue

        if char == b"<":
            if content[index + 1 : index + 2] == b"<":
                index = _skip_dictionary(content, index)
            else:
                raw, index = _hex_string(content, index)
                parts.append(_decode(raw))
            continue

        operator = _OPERATOR.match(content, index)
        if operator:
            if operator.group() in _BREAKS:
                parts.append("\n")
            index = operator.end()
            continue

        index += 1

    return _tidy("".join(parts))


def _literal(data: bytes, index: int) -> tuple[bytes, int]:
    """Read a `(...)` string, honouring escapes and balanced nested parens."""
    index += 1
    depth = 1
    out = bytearray()
    length = len(data)

    while index < length:
        byte = data[index]

        if byte == 0x5C:  # backslash
            index += 1
            if index >= length:
                break
            escaped = data[index]
            if escaped in _ESCAPES:
                out.append(_ESCAPES[escaped])
                index += 1
            elif 0x30 <= escaped <= 0x37:  # octal char code
                digits = bytearray()
                while index < length and len(digits) < 3 and 0x30 <= data[index] <= 0x37:
                    digits.append(data[index])
                    index += 1
                out.append(int(digits, 8) & 0xFF)
            elif escaped == 0x0A:  # line continuation
                index += 1
            elif escaped == 0x0D:
                index += 1
                if index < length and data[index] == 0x0A:
                    index += 1
            else:
                out.append(escaped)
                index += 1
            continue

        if byte == 0x28:  # (
            depth += 1
            out.append(byte)
        elif byte == 0x29:  # )
            depth -= 1
            index += 1
            if depth == 0:
                return bytes(out), index
            out.append(byte)
            continue
        else:
            out.append(byte)
        index += 1

    return bytes(out), index


def _hex_string(data: bytes, index: int) -> tuple[bytes, int]:
    close = data.find(b">", index)
    if close == -1:
        return b"", len(data)
    digits = re.sub(rb"[^0-9A-Fa-f]", b"", data[index + 1 : close])
    if len(digits) % 2:
        digits += b"0"
    try:
        return bytes.fromhex(digits.decode("ascii")), close + 1
    except ValueError:
        return b"", close + 1


def _skip_dictionary(data: bytes, index: int) -> int:
    close = data.find(b">>", index)
    return len(data) if close == -1 else close + 2


def _decode(raw: bytes) -> str:
    """Decode a PDF string. Hex strings from CID fonts are usually UTF-16BE."""
    if not raw:
        return ""
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    # Heuristic: alternating NULs is UTF-16BE without a byte-order mark.
    if len(raw) >= 4 and raw[0] == 0 and raw[2] == 0:
        return raw.decode("utf-16-be", errors="replace")
    return raw.decode("latin-1", errors="replace")


def _tidy(text: str) -> str:
    """Collapse the whitespace churn that operator-level extraction produces."""
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip()
