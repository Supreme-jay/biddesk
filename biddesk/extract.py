"""Pull individual requirements out of an RFP, tender, or questionnaire.

This stage is deliberately deterministic. Requirement extraction is where an
LLM is most tempting and least appropriate: if the extractor silently drops a
binding clause, the gap report is worse than useless because it reports false
confidence. Rules can be audited line by line; a model's omissions cannot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# "shall", "must" and "is required to" create contractual obligation. A gap on
# one of these is a different class of problem from a gap on "should".
BINDING = re.compile(
    r"\b(shall|must|is\s+required\s+to|are\s+required\s+to|will\s+be\s+required)\b", re.I
)
ADVISORY = re.compile(r"\b(should|may|is\s+expected\s+to|preferred|desirable)\b", re.I)

# Questionnaires ask in the imperative rather than with a modal or a question
# mark: "Describe your incident response process." Anchored to the start so
# "The Supplier shall provide..." is not caught here.
REQUEST = re.compile(
    r"^(describe|explain|outline|detail|provide|confirm|state|list|specify"
    r"|demonstrate|summaris[ez]e|set\s+out|give\s+details)\b", re.I
)

# Leading clause reference: "3.1.4", "A.2", "SR-14", "4)" etc.
CLAUSE_REF = re.compile(r"^((?:[A-Z]{1,4}[-.])?\d+(?:\.\d+)*[a-z]?)[.):\]]?\s+(?=\S)")

# Lines that look structural rather than substantive.
TOC_DOTS = re.compile(r"\.{4,}\s*\d+\s*$")
PAGE_NUM = re.compile(r"^(page\s+)?\d+(\s+of\s+\d+)?$", re.I)
ALL_CAPS_HEADING = re.compile(r"^[A-Z0-9 ,&/()-]{4,}$")

MIN_WORDS = 5


@dataclass
class Requirement:
    id: str
    text: str
    ref: str = ""
    kind: str = "clause"        # clause | question | row
    binding: bool = False
    line_no: int = 0
    tokens: list[str] = field(default_factory=list)

    @property
    def obligation(self) -> str:
        return "BINDING" if self.binding else "ADVISORY"


def extract(lines: list[str]) -> list[Requirement]:
    """Return every line that reads as an answerable requirement."""
    found: list[Requirement] = []
    seen: set[str] = set()

    for index, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not _is_candidate(line):
            continue

        ref, text = _split_ref(line)
        kind = _classify(line, text)
        if kind is None:
            continue

        key = _dedupe_key(text)
        if key in seen:
            continue
        seen.add(key)

        found.append(
            Requirement(
                id=f"R-{len(found) + 1:03d}",
                text=text,
                ref=ref,
                kind=kind,
                binding=bool(BINDING.search(text)),
                line_no=index,
            )
        )
    return found


def _is_candidate(line: str) -> bool:
    if len(line) < 15:
        return False
    if TOC_DOTS.search(line) or PAGE_NUM.match(line):
        return False
    # A heading in all caps with no sentence punctuation is structure, not a
    # requirement -- but keep it if it carries a binding verb anyway.
    if ALL_CAPS_HEADING.match(line) and not BINDING.search(line):
        return False
    return True


def _split_ref(line: str) -> tuple[str, str]:
    match = CLAUSE_REF.match(line)
    if not match:
        return "", line
    # In a table row the reference sits in its own cell, so lifting it out
    # leaves the cell separator stranded at the front of the requirement.
    return match.group(1), line[match.end():].lstrip("| ").strip()


def _classify(original: str, text: str) -> str | None:
    """Decide whether a line is a requirement, and of what kind."""
    words = len(text.split())

    # Spreadsheet rows: docparse joins cells with ' | '. Treat a row as a
    # requirement when any cell is a question or carries an obligation verb.
    if " | " in original:
        cells = [c.strip() for c in original.split("|")]
        asks = any(c.endswith("?") or REQUEST.match(c) for c in cells)
        if asks or BINDING.search(original):
            return "row" if words >= 4 else None
        return None

    if text.endswith("?") and words >= 4:
        return "question"

    if words < MIN_WORDS:
        return None

    if REQUEST.match(text):
        return "question"

    if BINDING.search(text) or ADVISORY.search(text):
        return "clause"

    return None


def _dedupe_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def summarise(requirements: list[Requirement]) -> dict[str, int]:
    return {
        "total": len(requirements),
        "binding": sum(1 for r in requirements if r.binding),
        "advisory": sum(1 for r in requirements if not r.binding),
        "questions": sum(1 for r in requirements if r.kind == "question"),
    }
