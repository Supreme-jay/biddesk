"""Draft every section of a grant application.

Unlike Bid Desk, ALL sections are drafted — including those with no evidence.
Grants are forward-looking planning documents, not contractual commitments.
Sections without evidence get [PLACEHOLDER] markers for the applicant to fill.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass

from .score import ScoredSection

MODEL = "claude-opus-5"
BATCH_SIZE = 5
MAX_TOKENS = 16000

SYSTEM_EVIDENCED = """\
You draft grant application sections grounded in the applicant's past work.

You will be given a question from a grant call and extracts from the
applicant's library of past applications and supporting documents.

Rules:
1. Ground every claim in the supplied extracts. Reuse specifics exactly:
   figures, dates, partner names, project titles.
2. Where a detail is needed but absent from the extracts, insert
   [PLACEHOLDER: brief description of what is needed] so the applicant
   knows exactly what to fill in.
3. Write in first person plural ("We will...", "Our approach...").
4. Match the tone of a grant application: clear, specific, forward-looking.
5. If a word limit is given, stay within it.
"""

SYSTEM_GENERATED = """\
You draft grant application sections from scratch.

You will be given a question from a grant call. The applicant has no prior
material for this topic, so you must write a well-structured draft they
can edit with their specific details.

Rules:
1. Use [PLACEHOLDER: description] markers for every specific fact, figure,
   partner name, date, or commitment that only the applicant can provide.
2. Write a complete, well-structured section — not bullet points or an outline.
3. Write in first person plural ("We will...", "Our approach...").
4. Match the tone of a grant application: clear, specific, forward-looking.
5. If a word limit is given, stay within it.
6. Provide a realistic structure the applicant can fill in, not generic prose.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "The section id, e.g. S-003"},
                    "answer": {"type": "string", "description": "The drafted section text"},
                    "placeholders": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of [PLACEHOLDER] items the applicant must fill",
                    },
                    "note": {
                        "type": "string",
                        "description": "Advice for the applicant on strengthening this section",
                    },
                },
                "required": ["id", "answer", "placeholders", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["drafts"],
    "additionalProperties": False,
}


class DraftingUnavailable(Exception):
    pass


@dataclass
class Draft:
    section_id: str
    answer: str
    source_tag: str
    placeholders: list[str]
    note: str = ""


def build_prompt_evidenced(batch: list[ScoredSection]) -> str:
    blocks = []
    for item in batch:
        sections = _relevant_sections(item)
        extracts = "\n\n".join(
            f"[Extract {n} - {chunk.citation}]\n{chunk.text}"
            for n, chunk in enumerate(sections, start=1)
        )
        ref = f" ({item.section.ref})" if item.section.ref else ""
        limit = f"\nWord limit: {item.section.word_limit}" if item.section.word_limit else ""
        blocks.append(
            f"### {item.section.id}{ref}\n"
            f"Question: {item.section.text}{limit}\n\n"
            f"Extracts from past applications:\n{extracts}"
        )
    return (
        "Draft a response for each section below, using its extracts as evidence.\n\n"
        + "\n\n---\n\n".join(blocks)
    )


def build_prompt_generated(batch: list[ScoredSection]) -> str:
    blocks = []
    for item in batch:
        ref = f" ({item.section.ref})" if item.section.ref else ""
        limit = f"\nWord limit: {item.section.word_limit}" if item.section.word_limit else ""
        weight = f"\nScoring weight: {item.section.score_weight}" if item.section.score_weight else ""
        blocks.append(
            f"### {item.section.id}{ref}\n"
            f"Question: {item.section.text}{limit}{weight}"
        )
    return (
        "Draft a response for each section below. Use [PLACEHOLDER: ...] for "
        "every specific fact only the applicant can provide.\n\n"
        + "\n\n---\n\n".join(blocks)
    )


def _relevant_sections(item: ScoredSection) -> list:
    if not item.evidence:
        return []
    wanted = set(item.section.tokens)
    supporting = [
        chunk for chunk in item.supporting
        if len(wanted & set(chunk.tokens)) >= 2
    ]
    return [item.evidence, *supporting]


def batches(results: list[ScoredSection], batch_size: int = BATCH_SIZE):
    evidenced = [r for r in results if r.source_tag == "EVIDENCED"]
    generated = [r for r in results if r.source_tag == "GENERATED"]

    ev_batches = [("evidenced", evidenced[i:i + batch_size])
                  for i in range(0, len(evidenced), batch_size)]
    gen_batches = [("generated", generated[i:i + batch_size])
                   for i in range(0, len(generated), batch_size)]
    return ev_batches + gen_batches


def preview(results: list[ScoredSection], batch_size: int = BATCH_SIZE):
    out = []
    for kind, batch in batches(results, batch_size):
        ids = [item.section.id for item in batch]
        if kind == "evidenced":
            prompt = build_prompt_evidenced(batch)
        else:
            prompt = build_prompt_generated(batch)
        out.append((kind, ids, prompt))
    return out


def generate(
    results: list[ScoredSection],
    effort: str = "high",
    batch_size: int = BATCH_SIZE,
    progress=None,
) -> list[Draft]:
    planned = batches(results, batch_size)
    if not planned:
        return []

    client = _client()
    drafts: list[Draft] = []
    total = len(planned)

    for number, (kind, batch) in enumerate(planned, start=1):
        ids = [item.section.id for item in batch]
        if progress:
            progress(number, total, ids, kind)

        if kind == "evidenced":
            system = SYSTEM_EVIDENCED
            prompt = build_prompt_evidenced(batch)
        else:
            system = SYSTEM_GENERATED
            prompt = build_prompt_generated(batch)

        drafts.extend(_run_batch(client, batch, system, prompt, kind, effort))

    return drafts


def _client():
    try:
        import anthropic
    except ImportError:
        raise DraftingUnavailable(
            "drafting needs the Anthropic SDK.\n"
            "  Install it with:  pip install anthropic\n"
        ) from None

    try:
        return anthropic.Anthropic()
    except Exception as exc:
        raise DraftingUnavailable(
            f"could not create the Anthropic client ({exc}). Set ANTHROPIC_API_KEY "
            "in the environment."
        ) from None


def _run_batch(client, batch, system, prompt, kind, effort) -> list[Draft]:
    import anthropic

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        raise DraftingUnavailable(
            "the API key was rejected. Check ANTHROPIC_API_KEY."
        ) from None
    except anthropic.RateLimitError as exc:
        retry = exc.response.headers.get("retry-after", "60")
        raise DraftingUnavailable(f"rate limited. Retry in {retry}s.") from None
    except anthropic.APIConnectionError:
        raise DraftingUnavailable("could not reach the API. Check the network.") from None
    except anthropic.APIStatusError as exc:
        raise DraftingUnavailable(f"API error {exc.status_code}: {exc.message}") from None

    return _parse(response, batch, kind)


def _parse(response, batch, kind) -> list[Draft]:
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise DraftingUnavailable(
            f"the model returned no text (stop_reason={response.stop_reason})."
        )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DraftingUnavailable(f"could not parse model response as JSON: {exc}") from None

    tag = "EVIDENCED" if kind == "evidenced" else "GENERATED"
    by_id = {}
    for entry in payload.get("drafts", []):
        by_id[entry.get("id", "")] = Draft(
            section_id=entry.get("id", ""),
            answer=entry.get("answer", "").strip(),
            source_tag=tag,
            placeholders=entry.get("placeholders", []),
            note=entry.get("note", "").strip(),
        )

    drafts = []
    for item in batch:
        found = by_id.get(item.section.id)
        drafts.append(found or Draft(
            section_id=item.section.id,
            answer="[This section needs to be written manually.]",
            source_tag=tag,
            placeholders=[],
            note="The model returned no draft for this section.",
        ))
    return drafts
