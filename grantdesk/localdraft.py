"""Local drafting engine — no API key, no network, no cost.

Produces structured grant application drafts using template assembly:
- EVIDENCED sections: weaves retrieved evidence into a coherent draft
- GENERATED sections: builds a structured template with [PLACEHOLDER] markers

Not as polished as an LLM draft, but fully functional, free, and offline.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

from .score import ScoredSection


@dataclass
class Draft:
    section_id: str
    answer: str
    source_tag: str
    placeholders: list[str]
    note: str = ""


def generate(results: list[ScoredSection]) -> list[Draft]:
    drafts = []
    for item in results:
        if item.source_tag == "EVIDENCED":
            drafts.append(_draft_evidenced(item))
        else:
            drafts.append(_draft_generated(item))
    return drafts


def _draft_evidenced(item: ScoredSection) -> Draft:
    sections = _relevant_sections(item)
    if not sections:
        return _draft_generated(item)

    question = item.section.text
    paras = []
    placeholders = []

    paras.append(
        f"Drawing on our established track record, we are well positioned "
        f"to address this requirement."
    )

    for chunk in sections:
        sentences = _extract_key_sentences(chunk.text, item.section.tokens)
        if sentences:
            paras.append(" ".join(sentences))

    if item.section.word_limit and _word_count("\n\n".join(paras)) > item.section.word_limit:
        paras = paras[:3]

    if _needs_specifics(question):
        placeholders.append("[PLACEHOLDER: Insert specific figures, dates, or metrics relevant to this grant]")
        paras.append(
            "[PLACEHOLDER: Add specific commitments, timelines, or deliverables "
            "tailored to this grant call.]"
        )

    answer = "\n\n".join(paras)
    return Draft(
        section_id=item.section.id,
        answer=answer,
        source_tag="EVIDENCED",
        placeholders=placeholders,
        note="Drafted from past material. Review for accuracy and tailor to this specific grant.",
    )


def _draft_generated(item: ScoredSection) -> Draft:
    question = item.section.text.strip()
    kind = item.section.kind
    placeholders = []

    if kind == "budget":
        return _template_budget(item)

    keywords = _extract_topic(question)
    topic = keywords if keywords else "this area"

    paras = []

    if _is_experience_question(question):
        paras.append(
            f"[PLACEHOLDER: Organisation name] has [PLACEHOLDER: X years] of experience "
            f"in {topic}. Our team brings expertise in [PLACEHOLDER: list key areas of "
            f"expertise relevant to {topic}]."
        )
        paras.append(
            "We have successfully delivered [PLACEHOLDER: number] projects of comparable "
            "scope, including [PLACEHOLDER: name 2-3 relevant past projects with outcomes]."
        )
        placeholders += [
            "Organisation name", "Years of experience",
            "Key areas of expertise", "Past project examples with outcomes",
        ]

    elif _is_problem_question(question):
        paras.append(
            f"The challenge we address is [PLACEHOLDER: describe the specific problem "
            f"in 2-3 sentences]. This affects [PLACEHOLDER: who is affected and how many] "
            f"and results in [PLACEHOLDER: quantify the impact — cost, time, quality]."
        )
        paras.append(
            "Current approaches fall short because [PLACEHOLDER: explain why existing "
            "solutions are inadequate]. Our project addresses this by [PLACEHOLDER: "
            "summarise your approach in one sentence]."
        )
        placeholders += [
            "Specific problem description", "Affected population/stakeholders",
            "Quantified impact", "Why current solutions fail", "Your approach summary",
        ]

    elif _is_solution_question(question):
        paras.append(
            "Our proposed solution is [PLACEHOLDER: one-sentence description of the solution]. "
            "It works by [PLACEHOLDER: explain the mechanism or approach in 2-3 sentences]."
        )
        paras.append(
            "What makes this innovative is [PLACEHOLDER: explain what is new or different "
            "compared to existing approaches]. This builds on [PLACEHOLDER: reference any "
            "prior work, research, or pilot that validates the approach]."
        )
        paras.append(
            "The key deliverables are:\n"
            "- [PLACEHOLDER: Deliverable 1 and its purpose]\n"
            "- [PLACEHOLDER: Deliverable 2 and its purpose]\n"
            "- [PLACEHOLDER: Deliverable 3 and its purpose]"
        )
        placeholders += [
            "Solution description", "How it works", "What makes it innovative",
            "Prior work or validation", "Key deliverables",
        ]

    elif _is_impact_question(question):
        paras.append(
            "We will measure success through [PLACEHOLDER: 3-5 specific, measurable KPIs]. "
            "Our evaluation methodology combines [PLACEHOLDER: quantitative and/or "
            "qualitative methods you will use]."
        )
        paras.append(
            "Key performance indicators include:\n"
            "- [PLACEHOLDER: KPI 1 — target and measurement method]\n"
            "- [PLACEHOLDER: KPI 2 — target and measurement method]\n"
            "- [PLACEHOLDER: KPI 3 — target and measurement method]"
        )
        placeholders += [
            "Specific KPIs", "Evaluation methodology", "KPI targets and measurement methods",
        ]

    elif _is_sustainability_question(question):
        paras.append(
            "Beyond the funding period, the project outcomes will be sustained through "
            "[PLACEHOLDER: describe your sustainability model — revenue, partnerships, "
            "institutional adoption, open-source, etc.]."
        )
        paras.append(
            "Our route to market is [PLACEHOLDER: explain how the solution reaches "
            "users/beneficiaries at scale]. We have already engaged [PLACEHOLDER: name "
            "partners, early adopters, or stakeholders who will support continuation]."
        )
        placeholders += [
            "Sustainability model", "Route to market", "Partners or early adopters",
        ]

    elif _is_risk_question(question):
        paras.append(
            "We have identified the following key risks and mitigations:\n\n"
            "| Risk | Likelihood | Impact | Mitigation |\n"
            "|---|---|---|---|\n"
            "| [PLACEHOLDER: Risk 1] | [Medium/High] | [Medium/High] | [PLACEHOLDER: Mitigation 1] |\n"
            "| [PLACEHOLDER: Risk 2] | [Medium/High] | [Medium/High] | [PLACEHOLDER: Mitigation 2] |\n"
            "| [PLACEHOLDER: Risk 3] | [Medium/High] | [Medium/High] | [PLACEHOLDER: Mitigation 3] |"
        )
        placeholders += ["Risk descriptions", "Likelihood and impact ratings", "Mitigation strategies"]

    elif _is_team_question(question):
        paras.append(
            "The project will be led by [PLACEHOLDER: Project Lead name and role], "
            "who brings [PLACEHOLDER: relevant experience]. The core team includes:"
        )
        paras.append(
            "- [PLACEHOLDER: Team member 1 — role, qualifications, relevant experience]\n"
            "- [PLACEHOLDER: Team member 2 — role, qualifications, relevant experience]\n"
            "- [PLACEHOLDER: Team member 3 — role, qualifications, relevant experience]"
        )
        paras.append(
            "We will ensure capacity through [PLACEHOLDER: recruitment plans, "
            "partnerships, or subcontracting arrangements]."
        )
        placeholders += [
            "Project lead details", "Team member details", "Capacity plans",
        ]

    elif _is_plan_question(question):
        paras.append(
            "The project is structured in [PLACEHOLDER: number] phases over "
            "[PLACEHOLDER: duration]:\n\n"
            "**Phase 1: [PLACEHOLDER: Phase name]** (Months 1-[X])\n"
            "- [PLACEHOLDER: Key activities]\n"
            "- Deliverable: [PLACEHOLDER: what is produced]\n\n"
            "**Phase 2: [PLACEHOLDER: Phase name]** (Months [X]-[Y])\n"
            "- [PLACEHOLDER: Key activities]\n"
            "- Deliverable: [PLACEHOLDER: what is produced]\n\n"
            "**Phase 3: [PLACEHOLDER: Phase name]** (Months [Y]-[Z])\n"
            "- [PLACEHOLDER: Key activities]\n"
            "- Deliverable: [PLACEHOLDER: what is produced]"
        )
        placeholders += [
            "Number of phases and duration", "Phase names and timelines",
            "Key activities per phase", "Deliverables per phase",
        ]

    else:
        paras.append(
            f"[PLACEHOLDER: Provide your response to the following: {_trim(question, 100)}]"
        )
        paras.append(
            "[PLACEHOLDER: Include specific evidence, examples, or data to "
            "support your answer. Reference any relevant experience, partnerships, "
            "or qualifications.]"
        )
        placeholders += ["Full response to the question", "Supporting evidence"]

    if item.section.word_limit:
        target = item.section.word_limit
        current = _word_count("\n\n".join(paras))
        if current < target * 0.5:
            paras.append(
                f"[PLACEHOLDER: Expand this section to approximately {target} words "
                f"with additional detail and evidence.]"
            )

    answer = "\n\n".join(paras)
    return Draft(
        section_id=item.section.id,
        answer=answer,
        source_tag="GENERATED",
        placeholders=placeholders,
        note="AI-generated template. Fill all [PLACEHOLDER] items with your specific details.",
    )


def _template_budget(item: ScoredSection) -> Draft:
    answer = (
        "**Project Budget Breakdown**\n\n"
        "| Category | Cost | Justification |\n"
        "|---|---|---|\n"
        "| Staff costs | [PLACEHOLDER: £X] | [PLACEHOLDER: roles and FTE] |\n"
        "| Equipment | [PLACEHOLDER: £X] | [PLACEHOLDER: what and why] |\n"
        "| Subcontracting | [PLACEHOLDER: £X] | [PLACEHOLDER: who and what for] |\n"
        "| Travel | [PLACEHOLDER: £X] | [PLACEHOLDER: purpose] |\n"
        "| Other | [PLACEHOLDER: £X] | [PLACEHOLDER: details] |\n"
        "| **Total** | **[PLACEHOLDER: £X]** | |\n\n"
        "[PLACEHOLDER: Add a narrative justification explaining why each cost "
        "is necessary and represents value for money.]"
    )
    return Draft(
        section_id=item.section.id,
        answer=answer,
        source_tag="GENERATED",
        placeholders=[
            "Staff costs and FTE breakdown",
            "Equipment costs and justification",
            "Subcontracting details",
            "Travel budget",
            "Total project cost",
            "Value for money narrative",
        ],
        note="Fill in all costs with actual figures. Ensure total matches the grant amount requested.",
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


def _extract_key_sentences(text: str, query_tokens: list[str]) -> list[str]:
    query_set = set(query_tokens)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    scored = []
    for s in sentences:
        words = set(re.findall(r'\b\w+\b', s.lower()))
        overlap = len(words & query_set)
        if overlap >= 2:
            scored.append((overlap, s.strip()))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:4]]


def _extract_topic(question: str) -> str:
    question = re.sub(r'\(max \d+ words.*?\)', '', question, flags=re.I)
    question = re.sub(r'\(worth \d+ marks.*?\)', '', question, flags=re.I)
    words = question.lower().split()
    stop = {'describe', 'explain', 'provide', 'detail', 'how', 'what', 'your',
            'will', 'you', 'the', 'a', 'an', 'is', 'are', 'of', 'in', 'to',
            'and', 'for', 'with', 'that', 'this', 'does', 'do', 'its', 'by'}
    meaningful = [w for w in words if w not in stop and len(w) > 2]
    return " ".join(meaningful[:5]) if meaningful else "this area"


def _is_experience_question(q: str) -> bool:
    return bool(re.search(r'\b(experience|track record|organisation|background|relevant)\b', q, re.I))

def _is_problem_question(q: str) -> bool:
    return bool(re.search(r'\b(problem|need|challenge|issue|gap|who benefits)\b', q, re.I))

def _is_solution_question(q: str) -> bool:
    return bool(re.search(r'\b(solution|approach|proposed|innovative|how does it work)\b', q, re.I))

def _is_impact_question(q: str) -> bool:
    return bool(re.search(r'\b(impact|measure|success|evaluat|kpi|performance indicator)\b', q, re.I))

def _is_sustainability_question(q: str) -> bool:
    return bool(re.search(r'\b(sustain|beyond|after|continue|route to market|adoption)\b', q, re.I))

def _is_risk_question(q: str) -> bool:
    return bool(re.search(r'\b(risk|mitigat|contingenc)\b', q, re.I))

def _is_team_question(q: str) -> bool:
    return bool(re.search(r'\b(team|staff|personnel|qualif|skills|capacity)\b', q, re.I))

def _is_plan_question(q: str) -> bool:
    return bool(re.search(r'\b(plan|milestone|timeline|deliverable|schedule|phase)\b', q, re.I))

def _needs_specifics(q: str) -> bool:
    return bool(re.search(r'\b(how|detail|specific|evidence|demonstrate)\b', q, re.I))

def _word_count(text: str) -> int:
    return len(text.split())

def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "..."
