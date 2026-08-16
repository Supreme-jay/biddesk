"""Optional drafting stage: write answers, but only where evidence exists.

This is the one part of Bid Desk that costs money and needs a network call, so
it is deliberately separate from everything else. The coverage matrix is the
product; drafting is a convenience layered on top of it.

Two rules make this safe to sell:

  1. **Gaps are never drafted.** A requirement with no supporting evidence is
     not sent to the model at all. There is nothing to ground an answer in, so
     any answer would be invention -- and invention in a tender is a binding
     commitment to a capability the bidder does not have.
  2. **The model can refuse.** Each draft carries `evidence_sufficient`. When
     the retrieved sections do not actually support an answer, the model says
     so instead of writing something plausible, and the caller sees the refusal.

The client supplies their own API key, so this costs the author nothing to
ship and nothing to demo.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

from .report import ANSWERED, WEAK, Assessment

MODEL = "claude-opus-5"
BATCH_SIZE = 5
MAX_TOKENS = 16000

SYSTEM = """\
You draft responses to procurement tender requirements for a bidding company.

You will be given a requirement and extracts from that company's library of past
proposals. Write the answer to the requirement using ONLY what those extracts
support.

Rules, in order of importance:

1. Never state a capability, certification, figure, or commitment that is not
   present in the supplied extracts. A tender response is contractually binding:
   an invented claim commits the company to something it cannot deliver.
2. If the extracts do not actually support an answer to the requirement, set
   evidence_sufficient to false and explain what is missing in `note`. Do not
   write a partial answer that reads as complete. Refusing is the correct and
   expected outcome whenever evidence is thin -- it is not a failure.
3. Reuse the specifics in the extracts exactly: certificate numbers, response
   times, percentages, named standards. Do not round, generalise, or restate
   them in your own words.
4. Where the extracts support the answer but omit a detail the requirement asks
   for, answer what you can, set evidence_sufficient to true, and name the
   missing detail in `note`.

Write in the third person ("The Supplier maintains..."), in plain British
English, 40-120 words per answer. No preamble, no headings, no marketing
language. Every answer is reviewed by a human before submission.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "The requirement id, e.g. R-014"},
                    "answer": {"type": "string", "description": "The drafted response text"},
                    "evidence_sufficient": {
                        "type": "boolean",
                        "description": "False when the extracts do not support an answer",
                    },
                    "note": {
                        "type": "string",
                        "description": "What is missing or needs human confirmation; '' if nothing",
                    },
                },
                "required": ["id", "answer", "evidence_sufficient", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["drafts"],
    "additionalProperties": False,
}


class DraftingUnavailable(Exception):
    """The drafting stage cannot run (missing package, credentials, or API error)."""


@dataclass
class Draft:
    requirement_id: str
    answer: str
    evidence_sufficient: bool
    note: str = ""


def draftable(results: list[Assessment]) -> list[Assessment]:
    """Requirements with enough evidence to ground an answer.

    Gaps are excluded by construction, not by prompt instruction -- they are
    never put in front of the model in the first place.
    """
    return [item for item in results if item.verdict in (ANSWERED, WEAK) and item.evidence]


def _relevant_sections(item: Assessment) -> list:
    """Best evidence, plus supporting sections that actually share vocabulary.

    Coverage scoring pools the top three sections deliberately — a requirement
    can be answered across several. Drafting is different: a section that merely
    ranked third is context the client pays for and the model must ignore. The
    best match always goes; the rest earn their place.
    """
    if not item.evidence:
        return []
    wanted = set(item.requirement.tokens)
    supporting = [
        chunk for chunk in item.supporting
        if len(wanted & set(chunk.tokens)) >= 2
    ]
    return [item.evidence, *supporting]


def build_prompt(batch: list[Assessment]) -> str:
    """Render one batch of requirements and their evidence."""
    blocks = []
    for item in batch:
        sections = _relevant_sections(item)
        extracts = "\n\n".join(
            f"[Extract {n} - {chunk.citation}]\n{chunk.text}"
            for n, chunk in enumerate(sections, start=1)
        )
        ref = f" (clause {item.requirement.ref})" if item.requirement.ref else ""
        blocks.append(
            f"### {item.requirement.id}{ref}\n"
            f"Requirement: {item.requirement.text}\n\n"
            f"Extracts from the proposal library:\n{extracts}"
        )
    return (
        "Draft a response for each requirement below, using only its own extracts.\n\n"
        + "\n\n---\n\n".join(blocks)
    )


def batches(results: list[Assessment], batch_size: int = BATCH_SIZE) -> list[list[Assessment]]:
    targets = draftable(results)
    return [targets[i : i + batch_size] for i in range(0, len(targets), batch_size)]


def preview(results: list[Assessment], batch_size: int = BATCH_SIZE) -> list[tuple[list[str], str]]:
    """Return the exact prompts that would be sent, without sending them.

    Returns rather than prints: this is a library, and a caller on a console
    that cannot encode the text should decide how to handle that, not discover
    it as a crash from inside a function that was only supposed to build strings.
    """
    return [
        ([item.requirement.id for item in batch], build_prompt(batch))
        for batch in batches(results, batch_size)
    ]


def generate(
    results: list[Assessment],
    effort: str = "high",
    batch_size: int = BATCH_SIZE,
    progress=None,
) -> list[Draft]:
    """Draft answers for every requirement that has supporting evidence."""
    planned = batches(results, batch_size)
    if not planned:
        return []

    client = _client()
    drafts: list[Draft] = []

    for number, batch in enumerate(planned, start=1):
        if progress:
            progress(number, len(planned), [item.requirement.id for item in batch])
        drafts.extend(_run_batch(client, batch, effort))

    return drafts


def _client():
    try:
        import anthropic
    except ImportError:
        raise DraftingUnavailable(
            "drafting needs the Anthropic SDK, which the rest of Bid Desk does not.\n"
            "  Install it with:  pip install anthropic\n"
            "  Scoring and the coverage report work without it."
        ) from None

    try:
        return anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001 - surfaced as an actionable message
        raise DraftingUnavailable(
            f"could not create the Anthropic client ({exc}). Set ANTHROPIC_API_KEY "
            "in the environment, or sign in with `ant auth login`."
        ) from None


def _run_batch(client, batch: list[Assessment], effort: str) -> list[Draft]:
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
                "text": SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": build_prompt(batch)}],
        )
    except anthropic.AuthenticationError:
        raise DraftingUnavailable(
            "the API key was rejected. Check ANTHROPIC_API_KEY, or run `ant auth login`."
        ) from None
    except anthropic.RateLimitError as exc:
        retry = exc.response.headers.get("retry-after", "60")
        raise DraftingUnavailable(
            f"rate limited by the API. Retry in {retry}s, or lower --batch-size."
        ) from None
    except anthropic.APIConnectionError:
        raise DraftingUnavailable(
            "could not reach the API. Check the network connection and retry."
        ) from None
    except anthropic.APIStatusError as exc:
        raise DraftingUnavailable(f"API error {exc.status_code}: {exc.message}") from None

    if response.stop_reason == "refusal":
        raise DraftingUnavailable(
            "the model declined to respond to this batch. Review the requirement text "
            "for content that may have tripped a safety classifier, then retry."
        )

    return _parse(response, batch)


def _parse(response, batch: list[Assessment]) -> list[Draft]:
    """Read the structured response, keeping unanswered requirements visible."""
    import json

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise DraftingUnavailable(
            f"the model returned no text (stop_reason={response.stop_reason}). "
            "If this is 'max_tokens', lower --batch-size."
        )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DraftingUnavailable(f"could not parse the model's response as JSON: {exc}") from None

    by_id = {}
    for entry in payload.get("drafts", []):
        by_id[entry.get("id", "")] = Draft(
            requirement_id=entry.get("id", ""),
            answer=entry.get("answer", "").strip(),
            evidence_sufficient=bool(entry.get("evidence_sufficient")),
            note=entry.get("note", "").strip(),
        )

    # A requirement dropped from the response is reported as undrafted rather
    # than silently vanishing -- a missing answer must not read as a finished one.
    drafts = []
    for item in batch:
        found = by_id.get(item.requirement.id)
        drafts.append(found or Draft(
            requirement_id=item.requirement.id,
            answer="",
            evidence_sufficient=False,
            note="The model returned no draft for this requirement.",
        ))
    return drafts


def write_markdown(drafts: list[Draft], results: list[Assessment], path) -> None:
    """Write the drafts with their requirement text, refusals called out first."""
    by_id = {item.requirement.id: item for item in results}
    usable = [d for d in drafts if d.evidence_sufficient and d.answer]
    refused = [d for d in drafts if not d.evidence_sufficient]

    lines = [
        "# Draft responses",
        "",
        f"{len(usable)} drafted from existing material · {len(refused)} need a human.",
        "",
        "Every answer below is a draft grounded in the cited extracts. None of it has "
        "been checked by a person. Requirements with no evidence on file were never "
        "sent for drafting and do not appear here -- see the coverage report for those.",
        "",
    ]

    if refused:
        lines += ["## Not drafted - evidence insufficient", ""]
        for draft in refused:
            item = by_id.get(draft.requirement_id)
            ref = f" ({item.requirement.ref})" if item and item.requirement.ref else ""
            lines.append(f"- **{draft.requirement_id}**{ref} — {draft.note or 'no reason given'}")
        lines.append("")

    lines += ["## Drafts", ""]
    for draft in usable:
        item = by_id.get(draft.requirement_id)
        if not item:
            continue
        ref = f" · clause {item.requirement.ref}" if item.requirement.ref else ""
        lines += [
            f"### {draft.requirement_id}{ref}",
            "",
            f"> {textwrap.shorten(item.requirement.text, 220, placeholder='…')}",
            "",
            draft.answer,
            "",
            f"*Source: {item.citation}*",
        ]
        if draft.note:
            lines.append(f"*Check before submitting: {draft.note}*")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
