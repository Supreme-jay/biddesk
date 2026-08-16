"""Turn scored requirements into the deliverable the client actually buys.

The headline output is not a drafted response -- it is the gap report. A bid
team's expensive question is "what in here can we not currently answer?", and
that is the question this module answers in a form they can act on.
"""

from __future__ import annotations

import csv
import html
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .extract import Requirement
from .retrieve import Chunk, Index, tokenise

ANSWERED, WEAK, GAP = "ANSWERED", "WEAK", "GAP"

# Calibrated on samples/ (see tests/evaluate.py), where covered requirements
# scored 0.45-1.00 and genuine gaps 0.17-0.49.
#
# ANSWERED_AT sits well clear of the highest observed gap (0.49) because the
# only unforgivable error is a gap marked ANSWERED -- it never reaches a human.
# The GAP/WEAK line is deliberately less precise: both route to review, so
# misplacing a requirement between them costs reviewer time, not the bid.
#
# These are corpus-dependent. A library with different house vocabulary shifts
# the distribution, so recalibrate per client with tests/evaluate.py against a
# labelled sample of their own material before trusting the defaults.
ANSWERED_AT = 0.62
WEAK_AT = 0.42

_RANK = {GAP: 0, WEAK: 1, ANSWERED: 2}


@dataclass
class Assessment:
    requirement: Requirement
    evidence: Chunk | None
    coverage: float
    relevance: float
    supporting: list[Chunk] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.coverage >= ANSWERED_AT:
            return ANSWERED
        if self.coverage >= WEAK_AT:
            return WEAK
        return GAP

    @property
    def critical(self) -> bool:
        """A binding obligation we hold no evidence for -- the bid-losing case."""
        return self.verdict == GAP and self.requirement.binding

    @property
    def citation(self) -> str:
        if not self.evidence:
            return "— no matching evidence —"
        if self.supporting:
            return f"{self.evidence.citation} (+{len(self.supporting)} more)"
        return self.evidence.citation


def assess(requirements: list[Requirement], index: Index) -> list[Assessment]:
    results = []
    for req in requirements:
        req.tokens = tokenise(req.text)
        best, supporting, coverage, relevance = index.evaluate(req.tokens)
        results.append(Assessment(req, best, coverage, relevance, supporting))
    return results


def tally(results: list[Assessment]) -> dict[str, int]:
    counts = {ANSWERED: 0, WEAK: 0, GAP: 0, "critical": 0}
    for item in results:
        counts[item.verdict] += 1
        if item.critical:
            counts["critical"] += 1
    return counts


def _sorted_for_action(results: list[Assessment]) -> list[Assessment]:
    """Critical gaps first -- the report should open on what can lose the bid."""
    return sorted(
        results,
        key=lambda a: (not a.critical, _RANK[a.verdict], a.coverage, a.requirement.id),
    )


def write_csv(results: list[Assessment], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["id", "ref", "obligation", "verdict", "critical",
             "coverage", "requirement", "evidence", "source_line"]
        )
        for item in _sorted_for_action(results):
            req = item.requirement
            writer.writerow([
                req.id, req.ref, req.obligation, item.verdict,
                "YES" if item.critical else "",
                f"{item.coverage:.2f}", req.text, item.citation, req.line_no,
            ])


def write_markdown(results: list[Assessment], path: Path, meta: dict) -> None:
    counts = tally(results)
    total = len(results) or 1
    lines = [
        f"# Bid coverage report — {meta.get('rfp', 'RFP')}",
        "",
        f"Generated {date.today().isoformat()} · {len(results)} requirements extracted "
        f"· {meta.get('chunks', 0)} evidence sections indexed",
        "",
        "## Position",
        "",
        f"- **Answered from existing material:** {counts[ANSWERED]} "
        f"({counts[ANSWERED] / total:.0%})",
        f"- **Weak — needs review:** {counts[WEAK]}",
        f"- **Gaps — nothing on file:** {counts[GAP]}",
        f"- **Critical (binding obligation, no evidence):** {counts['critical']}",
        "",
    ]

    critical = [a for a in results if a.critical]
    if critical:
        lines += [
            "## Critical gaps",
            "",
            "Binding obligations with no supporting evidence. Each of these must be "
            "answered by a human or the bid carries an unbacked commitment.",
            "",
        ]
        for item in critical:
            ref = f"{item.requirement.ref} " if item.requirement.ref else ""
            lines.append(f"- **{item.requirement.id}** {ref}— {item.requirement.text}")
        lines.append("")

    lines += ["## Full matrix", "",
              "| ID | Ref | Obligation | Verdict | Cov. | Requirement | Best evidence |",
              "|---|---|---|---|---|---|---|"]
    for item in _sorted_for_action(results):
        req = item.requirement
        flag = " ⚑" if item.critical else ""
        lines.append(
            f"| {req.id} | {req.ref or '—'} | {req.obligation} | {item.verdict}{flag} "
            f"| {item.coverage:.2f} | {_trim(req.text, 90)} | {_trim(item.citation, 50)} |"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_html(results: list[Assessment], path: Path, meta: dict) -> None:
    counts = tally(results)
    total = len(results) or 1
    rows = []
    for item in _sorted_for_action(results):
        req = item.requirement
        css = item.verdict.lower() + (" critical" if item.critical else "")
        rows.append(
            "<tr class='{css}'>"
            "<td class='id'>{id}</td><td class='id'>{ref}</td>"
            "<td><span class='ob {ob_css}'>{ob}</span></td>"
            "<td><span class='v'>{verdict}</span>{flag}</td>"
            "<td class='num'>{cov:.2f}</td><td>{text}</td><td class='ev'>{ev}</td>"
            "</tr>".format(
                css=css, id=html.escape(req.id), ref=html.escape(req.ref or "—"),
                ob_css=req.obligation.lower(), ob=req.obligation,
                verdict=item.verdict, flag=" <b class='flag'>CRITICAL</b>" if item.critical else "",
                cov=item.coverage, text=html.escape(req.text),
                ev=html.escape(item.citation),
            )
        )

    document = _HTML.format(
        title=html.escape(str(meta.get("rfp", "RFP"))),
        generated=date.today().isoformat(),
        extracted=len(results),
        indexed=meta.get("chunks", 0),
        answered=counts[ANSWERED], answered_pct=f"{counts[ANSWERED] / total:.0%}",
        weak=counts[WEAK], gap=counts[GAP], critical=counts["critical"],
        rows="\n".join(rows),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def _trim(text: str, limit: int) -> str:
    text = text.replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bid coverage — {title}</title>
<style>
:root {{
  --bg:#f5f7f6; --card:#fff; --ink:#16201c; --soft:#6b7a73; --rule:#dde4e0;
  --ok:#1e6b4a; --warn:#8a5d14; --bad:#97392c; --okbg:#e8f2ec; --warnbg:#f7eeda; --badbg:#f7e6e2;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg:#0e1411; --card:#151c18; --ink:#e2e9e5; --soft:#839389; --rule:#2a3630;
    --ok:#4fbf8b; --warn:#d3a445; --bad:#e0796a; --okbg:#1a2f24; --warnbg:#33290f; --badbg:#361f1a;
  }}
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 ui-sans-serif,"Segoe UI",system-ui,sans-serif;padding:2rem 1.25rem 4rem}}
.wrap{{max-width:1180px;margin:0 auto}}
h1{{font-size:1.6rem;margin:0 0 .3rem;letter-spacing:-.02em}}
.sub{{color:var(--soft);font-size:.86rem;margin-bottom:1.5rem}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;
  background:var(--rule);border:1px solid var(--rule);margin-bottom:1.75rem}}
.cards div{{background:var(--card);padding:.9rem 1rem}}
.cards dt{{font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--soft);margin-bottom:.3rem}}
.cards dd{{margin:0;font-size:1.5rem;font-weight:650;font-variant-numeric:tabular-nums}}
.cards .crit dd{{color:var(--bad)}}
.tw{{overflow-x:auto;border:1px solid var(--rule);background:var(--card)}}
table{{border-collapse:collapse;width:100%;min-width:900px;font-size:.85rem}}
th,td{{text-align:left;padding:.55rem .7rem;border-bottom:1px solid var(--rule);vertical-align:top}}
th{{font-size:.66rem;letter-spacing:.09em;text-transform:uppercase;color:var(--soft);font-weight:400;white-space:nowrap}}
td.id,td.num{{font-family:ui-monospace,Consolas,monospace;font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--soft)}}
td.ev{{color:var(--soft);font-size:.8rem}}
.v{{font-weight:650;font-size:.72rem;letter-spacing:.05em}}
tr.answered .v{{color:var(--ok)}} tr.weak .v{{color:var(--warn)}} tr.gap .v{{color:var(--bad)}}
tr.critical{{background:var(--badbg)}}
.flag{{font-size:.62rem;letter-spacing:.08em;color:var(--bad);margin-left:.4rem}}
.ob{{font-size:.64rem;letter-spacing:.07em;padding:.1rem .35rem;border:1px solid var(--rule);color:var(--soft)}}
.ob.binding{{color:var(--ink);border-color:var(--soft)}}
footer{{margin-top:1.5rem;color:var(--soft);font-size:.78rem;max-width:70ch}}
</style></head><body><div class="wrap">
<h1>Bid coverage — {title}</h1>
<div class="sub">Generated {generated} · {extracted} requirements extracted · {indexed} evidence sections indexed</div>
<dl class="cards">
<div><dt>Answered</dt><dd>{answered} <span style="font-size:.8rem;font-weight:400;color:var(--soft)">{answered_pct}</span></dd></div>
<div><dt>Weak — review</dt><dd>{weak}</dd></div>
<div><dt>Gaps</dt><dd>{gap}</dd></div>
<div class="crit"><dt>Critical</dt><dd>{critical}</dd></div>
</dl>
<div class="tw"><table>
<thead><tr><th>ID</th><th>Ref</th><th>Obligation</th><th>Verdict</th><th>Cov.</th><th>Requirement</th><th>Best evidence</th></tr></thead>
<tbody>
{rows}
</tbody></table></div>
<footer><strong>Critical</strong> means a binding obligation (&ldquo;shall&rdquo;, &ldquo;must&rdquo;)
for which no supporting evidence exists in the proposal library. Coverage is the
share of the requirement&rsquo;s distinctive vocabulary found in the best-matching
section. Nothing here is drafted text &mdash; every answer must still be written
or approved by a human.</footer>
</div></body></html>
"""
