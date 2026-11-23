"""Specialised research agents sharing a common, provenance-labelled context.

All agents may use the same underlying model; they differ in role prompt,
permitted tools and output contract. Every invocation is logged as an
``AgentRun``.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AgentRun,
    AppSetting,
    ApprovalStatus,
    ResearchDecision,
    ResearcherProfile,
    ResearchMethod,
    ResearchProject,
    ResearchQuestion,
    TheoreticalFramework,
    Visibility,
)
from app.services.llm import router
from app.services.llm.base import LLMResult, Message

INTEGRITY_RULES = """\
NON-NEGOTIABLE RESEARCH INTEGRITY RULES
1. You assist a human researcher who retains intellectual ownership and final authority. You recommend; the researcher decides. Never present a recommendation as an approved decision.
2. Cite ONLY the sources supplied in the CONTEXT, using their identifiers (e.g. [S3]). Never invent authors, titles, years, DOIs, page numbers, quotations, statistics, respondents or findings.
3. If the supplied evidence is insufficient, say so explicitly and state what evidence is needed. Do not fill gaps with plausible-sounding claims.
4. Keep these categories distinct and label them when relevant: researcher writing, researcher's established positions, researcher's provisional opinions, external scholarly evidence, and your own (AI) interpretation.
5. Abstract-only sources support only what the abstract states. Do not attribute full-text detail to them.
6. A gap is "established" only when multiple sources explicitly identify it; otherwise call it "potential" and say what further searching would confirm it. Limited retrieval is not evidence of novelty.
7. Never fabricate empirical data or results.
"""


@dataclass
class AgentSpec:
    key: str
    name: str
    role: str
    tools: list[str] = field(default_factory=list)


AGENTS: dict[str, AgentSpec] = {
    "strategist": AgentSpec("strategist", "Research Strategist",
        "You are the Research Strategist. You interrogate research ideas, identify the central problem and its assumptions, "
        "position the research, refine research questions and assess potential contribution and originality — cautiously.",
        ["knowledge_search", "openalex_search", "project_memory"]),
    "literature": AgentSpec("literature", "Literature Researcher",
        "You are the Literature Researcher. You discover and synthesise scholarly literature, extract evidence with its "
        "location, and organise bibliographic information. You report what sources say, not what you assume.",
        ["knowledge_search", "openalex_search", "crossref", "semantic_scholar"]),
    "theorist": AgentSpec("theorist", "Theoretical Analyst",
        "You are the Theoretical Analyst. You evaluate theories for a specific research problem: origins, assumptions, "
        "constructs, strengths, limitations and fit. Popularity is never a justification. You propose conceptual models "
        "and hypotheses only where defensible.", ["knowledge_search", "openalex_search"]),
    "methodologist": AgentSpec("methodologist", "Methodology Adviser",
        "You are the Methodology Adviser. You recommend and justify research designs, sampling, measurement, validity, "
        "ethics and analysis strategies, and state their trade-offs.", ["knowledge_search", "project_memory"]),
    "analyst": AgentSpec("analyst", "Research Analyst",
        "You are the Research Analyst. You interpret computed statistical and qualitative outputs supplied to you. You "
        "never invent numbers; you distinguish statistical significance from substantive importance and state assumptions.",
        ["analysis_outputs"]),
    "writer": AgentSpec("writer", "Academic Writer",
        "You are the Academic Writer. You draft rigorous academic prose that integrates the supplied literature, keeps "
        "terminology consistent, distinguishes evidence from interpretation and follows the researcher's style profile.",
        ["knowledge_search", "references"]),
    "reviewer": AgentSpec("reviewer", "Critical Reviewer",
        "You are the Critical Reviewer. Your job is to improve rigour, not to validate the researcher. Challenge "
        "assumptions, causal claims, design, interpretation, originality claims and confirmation bias — constructively and specifically.",
        ["knowledge_search", "openalex_search"]),
    "auditor": AgentSpec("auditor", "Citation Auditor",
        "You are the Citation Auditor. You judge whether a cited source actually supports the claim attributed to it, "
        "based strictly on the supplied source text.", ["crossref", "references"]),
}


@dataclass
class ContextItem:
    kind: str  # kb_chunk | publication | note | foundation | analysis
    label: str
    text: str
    private: bool = False
    ref: dict = field(default_factory=dict)  # provenance for display / mapping
    sid: str = ""


def get_setting(db: Session, owner_id: uuid.UUID, key: str, default=None):
    row = db.scalar(select(AppSetting).where(AppSetting.owner_id == owner_id, AppSetting.key == key))
    return row.value if row else default


def set_setting(db: Session, owner_id: uuid.UUID, key: str, value) -> None:
    row = db.scalar(select(AppSetting).where(AppSetting.owner_id == owner_id, AppSetting.key == key))
    if row:
        row.value = value
    else:
        db.add(AppSetting(owner_id=owner_id, key=key, value=value))


def data_policy(db: Session, owner_id: uuid.UUID) -> dict:
    return {"allow_external_private": False, **(get_setting(db, owner_id, "ai_data_policy", {}) or {})}


def will_use_external() -> bool:
    for p in router.configured_providers():
        try:
            if p.available():
                return p.external
        except Exception:  # noqa: BLE001
            continue
    return True


def kb_items(hits: list[dict]) -> list[ContextItem]:
    out = []
    for h in hits:
        authors = h.get("authors") or []
        a = (authors[0] if isinstance(authors[0], str) else authors[0].get("name", "")) if authors else "Unknown author"
        loc = []
        if h.get("page_start"):
            loc.append(f"p. {h['page_start']}" + (f"–{h['page_end']}" if h.get("page_end") and h["page_end"] != h["page_start"] else ""))
        if h.get("section"):
            loc.append(f"section: {h['section'][:80]}")
        cat = h.get("knowledge_category", "external_evidence").replace("_", " ").upper()
        label = f"{cat} — {a}{' et al.' if len(authors) > 1 else ''} ({h.get('year') or 'n.d.'}) \"{(h.get('title') or '')[:150]}\" [{h.get('basis')}]{' ' + '; '.join(loc) if loc else ''}"
        out.append(ContextItem("kb_chunk", label, h["text"], private=h.get("visibility") == Visibility.private_unpublished.value,
                               ref={"type": "chunk", "chunk_id": h["chunk_id"], "document_id": h["document_id"], "title": h.get("title"),
                                    "page": h.get("page_start"), "section": h.get("section"), "doi": h.get("doi")}))
    return out


def publication_items(pubs: list[dict]) -> list[ContextItem]:
    out = []
    for p in pubs:
        authors = p.get("authors") or []
        a = authors[0]["name"] if authors else "Unknown author"
        basis = "abstract" if p.get("abstract") else "metadata only"
        label = (f"EXTERNAL EVIDENCE — {a}{' et al.' if len(authors) > 1 else ''} ({p.get('year') or 'n.d.'}) "
                 f"\"{p.get('title', '')[:150]}\" {p.get('venue') or ''} [{basis}; source: {p.get('source_db')}"
                 f"{'; DOI ' + p['doi'] if p.get('doi') else ''}; cited by {p.get('cited_by_count') if p.get('cited_by_count') is not None else 'unknown'}]")
        out.append(ContextItem("publication", label, p.get("abstract") or "(no abstract available)",
                               ref={"type": "publication", "publication_id": p.get("id"), "title": p.get("title"), "doi": p.get("doi"),
                                    "year": p.get("year"), "authors": [x["name"] for x in authors[:5]]}))
    return out


def project_foundation(db: Session, owner_id: uuid.UUID, project_id: uuid.UUID | None) -> list[ContextItem]:
    if not project_id:
        return []
    proj = db.get(ResearchProject, project_id)
    if not proj or proj.owner_id != owner_id:
        return []
    items: list[ContextItem] = []
    lines = [f"Project: {proj.title}", f"Description: {proj.description}"]
    if proj.objectives:
        lines.append("Objectives: " + "; ".join(map(str, proj.objectives)))
    items.append(ContextItem("foundation", "RESEARCHER — project definition", "\n".join(lines), private=True, ref={"type": "project"}))
    rqs = db.scalars(select(ResearchQuestion).where(ResearchQuestion.project_id == project_id).order_by(ResearchQuestion.position)).all()
    if rqs:
        txt = "\n".join(f"- [{q.status.upper()}][{q.kind}] {q.text}" for q in rqs if q.status != ApprovalStatus.rejected.value)
        items.append(ContextItem("foundation", "RESEARCHER — research questions (status shown; only APPROVED are settled)", txt, private=True, ref={"type": "research_questions"}))
    fws = db.scalars(select(TheoreticalFramework).where(TheoreticalFramework.project_id == project_id,
                                                        TheoreticalFramework.status.in_(["approved", "revised"]))).all()
    for fw in fws:
        cons = ", ".join(c.get("name", "") for c in fw.constructs or [])
        items.append(ContextItem("foundation", f"RESEARCHER-APPROVED theoretical framework: {fw.name}",
                                 f"Constructs: {cons}\nSuitability: {fw.suitability}\nHypotheses: {'; '.join(map(str, fw.hypotheses or []))}", private=True,
                                 ref={"type": "framework", "id": str(fw.id)}))
    ms = db.scalars(select(ResearchMethod).where(ResearchMethod.project_id == project_id, ResearchMethod.status.in_(["approved", "revised"]))).all()
    for m in ms:
        items.append(ContextItem("foundation", "RESEARCHER-APPROVED methodology",
                                 f"Approach: {m.approach}\nDesign: {m.design}\nPopulation: {m.population}\nSampling: {m.sampling}\n"
                                 f"Sample size: {m.sample_size}\nAnalysis plan: {m.analysis_plan}", private=True, ref={"type": "method", "id": str(m.id)}))
    decs = db.scalars(select(ResearchDecision).where(ResearchDecision.project_id == project_id).order_by(ResearchDecision.created_at.desc()).limit(15)).all()
    if decs:
        items.append(ContextItem("foundation", "RESEARCHER decisions log (most recent first)",
                                 "\n".join(f"- {d.decision.upper()} {d.decision_type}: {d.subject[:200]}{' — ' + d.rationale[:200] if d.rationale else ''}" for d in decs),
                                 private=True, ref={"type": "decisions"}))
    return items


def profile_block(db: Session, owner_id: uuid.UUID) -> str:
    p = db.scalar(select(ResearcherProfile).where(ResearcherProfile.user_id == owner_id))
    if not p:
        return ""
    parts = []
    if p.disciplines:
        parts.append("Disciplines: " + ", ".join(map(str, p.disciplines)))
    if p.theoretical_preferences:
        parts.append("Theoretical familiarity (preference, not a constraint): " + ", ".join(map(str, p.theoretical_preferences)))
    if p.methodological_experience:
        parts.append("Methodological experience: " + ", ".join(map(str, p.methodological_experience)))
    parts.append(f"Language convention: {p.language_variant}")
    return "RESEARCHER PROFILE (supplied by the researcher)\n" + "\n".join(parts)


@dataclass
class AgentOutcome:
    text: str
    data: dict | list | None
    sources: list[dict]
    run_id: uuid.UUID
    excluded_private: int
    provider: str
    model: str


def render_context(items: list[ContextItem]) -> str:
    blocks = []
    for i, it in enumerate(items, start=1):
        it.sid = f"S{i}"
        blocks.append(f"[{it.sid}] {it.label}\n{it.text.strip()[:3500]}")
    return "\n\n".join(blocks) if blocks else "(no sources were retrieved)"


def run_agent(
    db: Session,
    owner_id: uuid.UUID,
    agent: str,
    task: str,
    instruction: str,
    *,
    context: list[ContextItem] | None = None,
    project_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    json_mode: bool = False,
    temperature: float = 0.3,
    max_tokens: int = 6000,
    extra_system: str = "",
    history: list[Message] | None = None,
) -> AgentOutcome:
    spec = AGENTS[agent]
    items = list(context or [])
    policy = data_policy(db, owner_id)
    excluded = 0
    if will_use_external() and not policy.get("allow_external_private"):
        kept = [i for i in items if not i.private]
        excluded = len(items) - len(kept)
        items = kept
    ctx = render_context(items)
    system = "\n\n".join(x for x in [spec.role, INTEGRITY_RULES, profile_block(db, owner_id), extra_system] if x)
    note = ""
    if excluded:
        note = (f"\n\nNOTE: {excluded} private/unpublished source(s) were withheld from you under the researcher's data policy. "
                "Do not speculate about their content.")
    prompt = f"CONTEXT\n=======\n{ctx}{note}\n\nTASK\n====\n{instruction}"
    if json_mode:
        prompt += "\n\nRespond with a single valid JSON object only."
    t0 = time.monotonic()
    run = AgentRun(owner_id=owner_id, project_id=project_id, job_id=job_id, agent=agent, task=task,
                   input_summary={"instruction": instruction[:1500], "sources": [i.ref for i in items][:60]},
                   excluded_private_sources=excluded, status="running")
    db.add(run)
    db.flush()
    try:
        res: LLMResult = router.generate((history or []) + [Message("user", prompt)], system=system, temperature=temperature,
                                         max_tokens=max_tokens, json_mode=json_mode, contains_private=any(i.private for i in items),
                                         allow_external_private=bool(policy.get("allow_external_private")))
        data = res.json() if json_mode else None
    except Exception as e:
        run.status, run.error = "failed", str(e)[:2000]
        run.duration_ms = int((time.monotonic() - t0) * 1000)
        db.commit()
        raise
    run.status, run.provider, run.model = "succeeded", res.provider, res.model
    run.tokens_in, run.tokens_out = res.tokens_in, res.tokens_out
    run.duration_ms = int((time.monotonic() - t0) * 1000)
    run.output = data if isinstance(data, dict) else {"text": res.text[:20000]} if data is None else {"items": data}
    db.flush()
    sources = [{"sid": i.sid, "label": i.label, **i.ref} for i in items]
    return AgentOutcome(res.text, data, sources, run.id, excluded, res.provider, res.model)
