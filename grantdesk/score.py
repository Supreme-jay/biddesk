"""Score grant sections against a library of past applications.

Thin wrapper over biddesk.retrieve — the BM25 engine and corpus loader are
identical. The only difference is the threshold: grants tag sections as
EVIDENCED or GENERATED rather than ANSWERED/WEAK/GAP, and the consequence
of a wrong tag is a review flag, not a legal commitment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from biddesk.retrieve import Chunk, Index, load_corpus, tokenise

from .extract import Section

EVIDENCED_AT = 0.45


@dataclass
class ScoredSection:
    section: Section
    evidence: Chunk | None
    supporting: list[Chunk] = field(default_factory=list)
    coverage: float = 0.0
    relevance: float = 0.0

    @property
    def source_tag(self) -> str:
        return "EVIDENCED" if self.coverage >= EVIDENCED_AT else "GENERATED"


def score_all(sections: list[Section], index: Index) -> list[ScoredSection]:
    results = []
    for sec in sections:
        sec.tokens = tokenise(sec.text)
        best, supporting, coverage, relevance = index.evaluate(sec.tokens)
        results.append(ScoredSection(sec, best, supporting, coverage, relevance))
    return results
