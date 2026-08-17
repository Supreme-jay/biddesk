"""Pull individual sections/questions out of a grant call or funding brief.

Grant calls differ from tenders: they ask "describe", "explain", "set out"
rather than "shall"/"must". They carry word/page limits and scoring weights.
There are no binding obligations — everything is forward-looking planning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PROMPT = re.compile(
    r"\b(describe|explain|outline|detail|provide"
    r"|confirm|state|list|specify|demonstrate|summaris[ez]e"
    r"|set\s+out|give\s+details|tell\s+us|show\s+how"
    r"|evidence|justify|indicate|illustrate|identify"
    r"|what\s+is\s+your|how\s+will\s+you|how\s+do\s+you"
    r"|what\s+are\s+your|what\s+steps|what\s+measures"
    r"|please\s+provide|please\s+describe|please\s+explain)\b", re.I
)

CONTAINS_QUESTION = re.compile(r"\?\s")

WORD_LIMIT = re.compile(
    r"(?:max(?:imum)?|up\s+to|no\s+more\s+than|limit[: ]*)\s*(\d{2,5})\s*words?", re.I
)
PAGE_LIMIT = re.compile(
    r"(?:max(?:imum)?|up\s+to|no\s+more\s+than|limit[: ]*)\s*(\d{1,2})\s*pages?", re.I
)

SCORE_WEIGHT = re.compile(
    r"(?:worth|weighted?|scores?|marks?|points?)[: ]*\s*(\d{1,3})\s*(?:%|marks?|points?|/)", re.I
)
SCORE_WEIGHT_ALT = re.compile(
    r"(\d{1,3})\s*(?:%|marks?|points?)\s*(?:weighting|weight|of\s+total)", re.I
)

SECTION_REF = re.compile(
    r"^((?:section\s+|q(?:uestion)?\s*)?(?:[A-Z]{0,4}[-.])?\d+(?:\.\d+)*[a-z]?)[.):\]]\s+(?=\S)", re.I
)

TOC_DOTS = re.compile(r"\.{4,}\s*\d+\s*$")
PAGE_NUM = re.compile(r"^(page\s+)?\d+(\s+of\s+\d+)?$", re.I)
ALL_CAPS_HEADING = re.compile(r"^[A-Z0-9 ,&/()-]{4,}$")

MIN_WORDS = 5


@dataclass
class Section:
    id: str
    text: str
    ref: str = ""
    kind: str = "narrative"     # narrative | question | checklist | budget
    word_limit: int | None = None
    page_limit: int | None = None
    score_weight: str = ""
    line_no: int = 0
    tokens: list[str] = field(default_factory=list)


def extract(lines: list[str]) -> list[Section]:
    found: list[Section] = []
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

        wl = WORD_LIMIT.search(line)
        pl = PAGE_LIMIT.search(line)
        sw = SCORE_WEIGHT.search(line) or SCORE_WEIGHT_ALT.search(line)

        found.append(Section(
            id=f"S-{len(found) + 1:03d}",
            text=text,
            ref=ref,
            kind=kind,
            word_limit=int(wl.group(1)) if wl else None,
            page_limit=int(pl.group(1)) if pl else None,
            score_weight=sw.group(0).strip() if sw else "",
            line_no=index,
        ))
    return found


def _is_candidate(line: str) -> bool:
    if len(line) < 15:
        return False
    if TOC_DOTS.search(line) or PAGE_NUM.match(line):
        return False
    if ALL_CAPS_HEADING.match(line) and not PROMPT.search(line):
        return False
    return True


def _split_ref(line: str) -> tuple[str, str]:
    match = SECTION_REF.match(line)
    if not match:
        return "", line
    return match.group(1), line[match.end():].lstrip("| ").strip()


def _classify(original: str, text: str) -> str | None:
    words = len(text.split())

    if " | " in original:
        cells = [c.strip() for c in original.split("|")]
        asks = any(c.endswith("?") or PROMPT.match(c) for c in cells)
        if asks or PROMPT.search(original):
            return "checklist" if words >= 4 else None
        return None

    if (text.endswith("?") or CONTAINS_QUESTION.search(text)) and words >= 4:
        return "question"

    if words < MIN_WORDS:
        return None

    if PROMPT.search(text):
        return "narrative"

    if re.search(r"\b(budget|cost|expenditure|financial\s+plan|funding\s+breakdown)\b", text, re.I):
        return "budget"

    return None


def _dedupe_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def summarise(sections: list[Section]) -> dict[str, int]:
    return {
        "total": len(sections),
        "narrative": sum(1 for s in sections if s.kind == "narrative"),
        "questions": sum(1 for s in sections if s.kind == "question"),
        "checklist": sum(1 for s in sections if s.kind == "checklist"),
        "budget": sum(1 for s in sections if s.kind == "budget"),
        "with_word_limit": sum(1 for s in sections if s.word_limit),
        "with_score_weight": sum(1 for s in sections if s.score_weight),
    }
