"""Index past proposals and score how well they answer each requirement.

Two numbers come out of this module and they do different jobs:

  * BM25 ranks candidate evidence -- it decides *which* past section is the
    best match for a requirement.
  * IDF-weighted term coverage decides *whether* that match is good enough to
    call the requirement answered. It is bounded 0..1, so it can be thresholded
    and, more importantly, explained to a client: "62% of the distinctive
    vocabulary in this requirement appears in the evidence we hold."

An unbounded relevance score cannot support the claim "this is a gap", which
is the only claim the buyer is actually paying for.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import docparse

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "for", "with", "by", "from", "as", "is", "are", "was", "were", "be", "been",
    "being", "it", "its", "this", "that", "these", "those", "any", "all", "each",
    "we", "our", "us", "you", "your", "they", "their", "there", "which", "who",
    "what", "when", "where", "how", "not", "no", "can", "have", "has", "had",
    "do", "does", "did", "so", "such", "than", "then", "also", "into", "within",
    "per", "via", "including", "include", "includes", "other", "please",
    # Obligation and role words appear on nearly every line of an RFP, so they
    # carry no discriminative signal and would flatten every score.
    "shall", "must", "should", "may", "will", "would", "required", "require",
    "requires", "requirement", "requirements", "supplier", "suppliers", "vendor",
    "vendors", "contractor", "bidder", "tenderer", "proposal", "response",
    "respond", "describe", "detail", "provide", "confirm", "state", "outline",
    # Contract boilerplate. These are near-universal in tender language and
    # carry no topical signal, but because they are often *absent* from a
    # proposal library they otherwise inflate the "unmatched" side of the
    # score and bury the one word that actually decides the requirement.
    "accordance", "associated", "bear", "affected", "ensure", "undertake",
    "submit", "deliver", "apply", "applicable", "appropriate", "relevant",
    "additional", "agreed", "minimum", "maximum", "current", "duration",
    "throughout", "prior", "written", "contract", "authority", "party",
    "parties", "term", "terms", "condition", "conditions", "thereafter",
    "herein", "whereby", "hereunder", "said", "aforementioned", "exceeding",
    "less", "not", "no", "least", "case", "event", "basis", "means",
}

_WORD = re.compile(r"[a-z][a-z0-9+#.-]*")


def tokenise(text: str) -> list[str]:
    out = []
    for word in _WORD.findall(text.lower()):
        word = word.strip(".-")
        if len(word) < 2 or word in STOPWORDS:
            continue
        out.append(_stem(word))
    return out


def _stem(word: str) -> str:
    """Deliberately light stemming -- predictable beats linguistically correct."""
    for suffix, keep in (("ing", 5), ("ed", 5), ("ies", 5), ("es", 5), ("s", 4)):
        if len(word) > keep and word.endswith(suffix):
            if suffix == "ies":
                return word[:-3] + "y"
            return word[: -len(suffix)]
    return word


@dataclass
class Chunk:
    doc: str
    heading: str
    text: str
    tokens: list[str]

    @property
    def citation(self) -> str:
        return f"{self.doc} → {self.heading}" if self.heading else self.doc


def load_corpus(directory: Path) -> tuple[list[Chunk], list[tuple[str, str]]]:
    """Read every supported document in a directory into heading-level chunks.

    Returns (chunks, skipped) where skipped is [(filename, reason)].

    Skipped files must never be swallowed. A proposal document that fails to
    parse is missing evidence, so every requirement it answered is reported as
    a gap -- the report is confidently wrong in the direction the client will
    act on. The caller is expected to surface these loudly.
    """
    chunks: list[Chunk] = []
    skipped: list[tuple[str, str]] = []

    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in docparse.SUPPORTED:
            if path.suffix:  # ignore extensionless helpers, flag real documents
                skipped.append((path.name, f"unsupported format '{path.suffix}'"))
            continue
        try:
            lines = docparse.normalise(docparse.read(path))
        except docparse.UnsupportedDocument as exc:
            reason = str(exc).split(": ", 1)[-1]
            skipped.append((path.name, reason))
            continue

        found = _chunk(path.name, lines)
        if not found:
            skipped.append((path.name, "no readable text found"))
            continue
        chunks.extend(found)

    return chunks, skipped


def _chunk(doc: str, lines: list[str]) -> list[Chunk]:
    """Split a document on headings; fall back to one chunk per document."""
    sections: list[tuple[str, list[str]]] = []
    heading = ""
    body: list[str] = []

    for line in lines:
        if _is_heading(line):
            if body:
                sections.append((heading, body))
                body = []
            heading = line.lstrip("# ").strip()
        else:
            body.append(line)
    if body:
        sections.append((heading, body))

    out = []
    for head, content in sections:
        text = " ".join(content).strip()
        if len(text) < 40:
            continue
        # The heading is repeated into the body so its words are searchable.
        out.append(Chunk(doc, head, text, tokenise(f"{head} {text}")))
    return out


def _is_heading(line: str) -> bool:
    if line.startswith("#"):
        return True
    return len(line.split()) <= 8 and not line.endswith((".", ";", ":", "?")) and line[:1].isupper()


class Index:
    """BM25 over corpus chunks, plus bounded coverage scoring."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.n = len(chunks)
        self.freqs = [Counter(c.tokens) for c in chunks]
        self.lengths = [len(c.tokens) for c in chunks]
        self.avg_len = (sum(self.lengths) / self.n) if self.n else 0.0

        doc_freq: Counter[str] = Counter()
        for chunk in chunks:
            doc_freq.update(set(chunk.tokens))
        self.doc_freq = doc_freq
        self.idf = {
            term: math.log(1 + (self.n - count + 0.5) / (count + 0.5))
            for term, count in doc_freq.items()
        }
        # A query term absent from the whole corpus still counts against
        # coverage -- we genuinely have no material using it -- but it is capped
        # at the weight of the rarest term we *have* seen (df=1) rather than the
        # df=0 extreme. Uncapped, incidental phrasing outweighs the decisive
        # domain word and turns covered requirements into false gaps.
        self.unseen_idf = math.log(1 + (self.n - 0.5) / 1.5) if self.n else 1.0

    def bm25(self, query: list[str], i: int) -> float:
        if not self.n or not self.avg_len:
            return 0.0
        freqs, length = self.freqs[i], self.lengths[i]
        score = 0.0
        for term in query:
            f = freqs.get(term, 0)
            if not f:
                continue
            denom = f + self.k1 * (1 - self.b + self.b * length / self.avg_len)
            score += self.idf.get(term, 0.0) * (f * (self.k1 + 1)) / denom
        return score

    def coverage(self, query: list[str], present: set[str]) -> float:
        """Share of a requirement's distinctive vocabulary found in the evidence.

        Weights are idf **squared**. Linear idf treats a near-conclusive rare
        term ("TUPE", "CREST", "BPSS") as merely one term among ten, so a
        requirement whose defining word is sitting in the corpus still scores as
        a gap. Squaring widens the gap between a rare term and a generic one
        from roughly 4x to 16x, which is what makes the decisive word decisive.
        The metric stays bounded 0..1 and still explains as "share of
        distinctive vocabulary matched".
        """
        terms = set(query)
        if not terms:
            return 0.0
        weight = {t: self.idf.get(t, self.unseen_idf) ** 2 for t in terms}
        total = sum(weight.values())
        if total <= 0:
            return 0.0
        return sum(w for t, w in weight.items() if t in present) / total

    def evaluate(self, query: list[str], top_k: int = 3):
        """Score a requirement against the best `top_k` sections pooled.

        Bid answers are routinely assembled from several parts of the proposal
        library, so scoring against a single chunk understates real coverage.
        Ranking still picks one section to cite; coverage is measured against
        the union of the top few.

        Returns (best_chunk, supporting_chunks, coverage, bm25).
        """
        if not self.n or not query:
            return None, [], 0.0, 0.0

        scored = [(self.bm25(query, i), i) for i in range(self.n)]
        scored = [pair for pair in scored if pair[0] > 0]
        if not scored:
            return None, [], 0.0, 0.0
        scored.sort(reverse=True)

        chosen = scored[:top_k]
        pooled: set[str] = set()
        for _, i in chosen:
            pooled |= set(self.freqs[i])

        best_score, best_i = chosen[0]
        supporting = [self.chunks[i] for _, i in chosen[1:]]
        return self.chunks[best_i], supporting, self.coverage(query, pooled), best_score
