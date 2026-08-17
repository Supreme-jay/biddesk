"""Render the grant application draft as a ready-to-edit document.

Unlike Bid Desk's coverage matrix, this produces the actual application:
every section drafted, tagged by source, ready for human review.
"""

from __future__ import annotations

import csv
import html
from datetime import date
from pathlib import Path

from .draft import Draft
from .score import ScoredSection


def write_markdown(
    drafts: list[Draft],
    results: list[ScoredSection],
    path: Path,
    meta: dict,
) -> None:
    by_id = {item.section.id: item for item in results}
    evidenced = [d for d in drafts if d.source_tag == "EVIDENCED"]
    generated = [d for d in drafts if d.source_tag == "GENERATED"]

    lines = [
        f"# Grant application draft — {meta.get('call', 'Grant Call')}",
        "",
        f"Generated {date.today().isoformat()} · {len(drafts)} sections drafted",
        "",
        f"- **From past material:** {len(evidenced)}",
        f"- **AI-generated (needs review):** {len(generated)}",
        "",
        "---",
        "",
    ]

    for draft in drafts:
        item = by_id.get(draft.section_id)
        if not item:
            continue

        ref = f" · {item.section.ref}" if item.section.ref else ""
        tag = f"[{draft.source_tag}]"
        limit = f" · max {item.section.word_limit} words" if item.section.word_limit else ""
        weight = f" · {item.section.score_weight}" if item.section.score_weight else ""

        lines += [
            f"## {draft.section_id}{ref} {tag}",
            "",
            f"> {item.section.text}{limit}{weight}",
            "",
            draft.answer,
            "",
        ]

        if draft.placeholders:
            lines.append("**Placeholders to fill:**")
            for ph in draft.placeholders:
                lines.append(f"- {ph}")
            lines.append("")

        if draft.note:
            lines.append(f"*Note: {draft.note}*")
            lines.append("")

        lines.append("---")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(
    drafts: list[Draft],
    results: list[ScoredSection],
    path: Path,
) -> None:
    by_id = {item.section.id: item for item in results}
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "id", "ref", "kind", "source_tag", "coverage",
            "word_limit", "score_weight", "question", "draft", "placeholders", "note",
        ])
        for draft in drafts:
            item = by_id.get(draft.section_id)
            if not item:
                continue
            writer.writerow([
                draft.section_id,
                item.section.ref,
                item.section.kind,
                draft.source_tag,
                f"{item.coverage:.2f}",
                item.section.word_limit or "",
                item.section.score_weight,
                item.section.text,
                draft.answer,
                "; ".join(draft.placeholders),
                draft.note,
            ])


def write_html(
    drafts: list[Draft],
    results: list[ScoredSection],
    path: Path,
    meta: dict,
) -> None:
    by_id = {item.section.id: item for item in results}
    evidenced = sum(1 for d in drafts if d.source_tag == "EVIDENCED")
    generated = sum(1 for d in drafts if d.source_tag == "GENERATED")

    sections_html = []
    for draft in drafts:
        item = by_id.get(draft.section_id)
        if not item:
            continue

        tag_css = "ev" if draft.source_tag == "EVIDENCED" else "gen"
        tag_label = "From past material" if draft.source_tag == "EVIDENCED" else "AI-generated — needs review"
        ref = html.escape(item.section.ref) if item.section.ref else ""
        limit = f"<span class='meta'>Max {item.section.word_limit} words</span>" if item.section.word_limit else ""
        weight = f"<span class='meta'>{html.escape(item.section.score_weight)}</span>" if item.section.score_weight else ""

        placeholders = ""
        if draft.placeholders:
            ph_items = "".join(f"<li>{html.escape(p)}</li>" for p in draft.placeholders)
            placeholders = f"<div class='ph'><strong>Placeholders to fill:</strong><ul>{ph_items}</ul></div>"

        note = f"<div class='note'>{html.escape(draft.note)}</div>" if draft.note else ""

        sections_html.append(
            f"<div class='section {tag_css}'>"
            f"<div class='hdr'>"
            f"<span class='sid'>{html.escape(draft.section_id)}</span>"
            f"{'<span class=\"ref\">' + ref + '</span>' if ref else ''}"
            f"<span class='tag {tag_css}'>{tag_label}</span>"
            f"{limit}{weight}"
            f"</div>"
            f"<blockquote>{html.escape(item.section.text)}</blockquote>"
            f"<div class='body'>{_nl2br(html.escape(draft.answer))}</div>"
            f"{placeholders}{note}"
            f"</div>"
        )

    document = _HTML.format(
        title=html.escape(str(meta.get("call", "Grant Call"))),
        generated=date.today().isoformat(),
        total=len(drafts),
        evidenced=evidenced,
        generated_count=generated,
        sections="\n".join(sections_html),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def write_analysis_md(
    results: list[ScoredSection],
    path: Path,
    meta: dict,
) -> None:
    evidenced = [r for r in results if r.source_tag == "EVIDENCED"]
    generated = [r for r in results if r.source_tag == "GENERATED"]

    lines = [
        f"# Grant call analysis — {meta.get('call', 'Grant Call')}",
        "",
        f"Generated {date.today().isoformat()} · {len(results)} sections found "
        f"· {meta.get('chunks', 0)} library sections indexed",
        "",
        f"- **Reusable from past applications:** {len(evidenced)}",
        f"- **Need new material:** {len(generated)}",
        "",
        "## Sections",
        "",
        "| ID | Ref | Kind | Source | Cov. | Question |",
        "|---|---|---|---|---|---|",
    ]
    for item in results:
        sec = item.section
        lines.append(
            f"| {sec.id} | {sec.ref or '—'} | {sec.kind} | {item.source_tag} "
            f"| {item.coverage:.2f} | {_trim(sec.text, 80)} |"
        )
    lines += [
        "",
        "Run `grantdesk draft` to generate a complete application draft.",
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _trim(text: str, limit: int) -> str:
    text = text.replace("|", "\\|")
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _nl2br(text: str) -> str:
    return text.replace("\n", "<br>")


_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Grant draft — {title}</title>
<style>
:root {{
  --bg:#f5f7f6; --card:#fff; --ink:#16201c; --soft:#6b7a73; --rule:#dde4e0;
  --ok:#1e6b4a; --warn:#8a5d14; --okbg:#e8f2ec; --warnbg:#fff8e8;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg:#0e1411; --card:#151c18; --ink:#e2e9e5; --soft:#839389; --rule:#2a3630;
    --ok:#4fbf8b; --warn:#d3a445; --okbg:#1a2f24; --warnbg:#33290f;
  }}
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 ui-sans-serif,"Segoe UI",system-ui,sans-serif;padding:2rem 1.25rem 4rem}}
.wrap{{max-width:860px;margin:0 auto}}
h1{{font-size:1.5rem;margin:0 0 .3rem}}
.sub{{color:var(--soft);font-size:.86rem;margin-bottom:1.5rem}}
.cards{{display:flex;gap:1rem;margin-bottom:2rem;flex-wrap:wrap}}
.cards div{{background:var(--card);border:1px solid var(--rule);padding:.7rem 1rem;min-width:160px}}
.cards dt{{font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--soft);margin-bottom:.2rem}}
.cards dd{{margin:0;font-size:1.4rem;font-weight:650}}
.section{{background:var(--card);border:1px solid var(--rule);margin-bottom:1rem;padding:1.2rem 1.4rem}}
.hdr{{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-bottom:.6rem}}
.sid{{font-family:ui-monospace,Consolas,monospace;font-weight:600;font-size:.85rem}}
.ref{{color:var(--soft);font-size:.8rem}}
.tag{{font-size:.65rem;letter-spacing:.06em;padding:.15rem .45rem;font-weight:600;text-transform:uppercase}}
.tag.ev{{background:var(--okbg);color:var(--ok)}}
.tag.gen{{background:var(--warnbg);color:var(--warn)}}
.meta{{font-size:.75rem;color:var(--soft)}}
blockquote{{margin:.5rem 0;padding:.5rem .8rem;border-left:3px solid var(--rule);color:var(--soft);font-size:.9rem}}
.body{{line-height:1.7;margin:.8rem 0}}
.ph{{margin-top:.8rem;font-size:.85rem}} .ph ul{{margin:.3rem 0;padding-left:1.3rem}}
.ph li{{margin:.2rem 0}}
.note{{margin-top:.6rem;font-size:.85rem;color:var(--warn);font-style:italic}}
footer{{margin-top:2rem;color:var(--soft);font-size:.78rem;max-width:70ch}}
</style></head><body><div class="wrap">
<h1>Grant application draft — {title}</h1>
<div class="sub">Generated {generated} · {total} sections drafted</div>
<dl class="cards">
<div><dt>From past material</dt><dd>{evidenced}</dd></div>
<div><dt>AI-generated</dt><dd>{generated_count}</dd></div>
</dl>
{sections}
<footer>Sections tagged <strong>EVIDENCED</strong> were drafted from the applicant's
past material. Sections tagged <strong>AI-GENERATED</strong> were written from scratch
and contain [PLACEHOLDER] markers for specific details only the applicant can provide.
Every section must be reviewed by a human before submission.</footer>
</div></body></html>
"""
