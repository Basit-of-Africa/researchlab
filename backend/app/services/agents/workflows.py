"""Coordinated agent workflows built with LangGraph.

Each workflow is a small StateGraph whose nodes are agent invocations or
deterministic tool steps (retrieval). Agents share the same project context.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.services.agents.core import (
    ContextItem,
    kb_items,
    project_foundation,
    publication_items,
    run_agent,
)
from app.services.scholarly import clients
from app.services.scholarly.clients import ScholarlyError
from app.services.scholarly.service import publication_to_dict, upsert_publication
from app.services.search import SearchFilters, hybrid_search, search_notes

log = logging.getLogger("researchlab.agents")


# ---------------------------------------------------------------------------
# Shared retrieval tool
# ---------------------------------------------------------------------------
def gather_sources(db: Session, owner_id: uuid.UUID, queries: list[str], *, project_id=None, use_openalex: bool = True,
                   kb_k: int = 6, oa_k: int = 6, year_from: int | None = None) -> dict:
    kb: dict[str, dict] = {}
    pubs: dict[str, dict] = {}
    notes: dict[str, dict] = {}
    warnings: list[str] = []
    for q in [q for q in queries if q and q.strip()][:6]:
        for h in hybrid_search(db, owner_id, q, SearchFilters(), k=kb_k):
            kb.setdefault(h["chunk_id"], h)
        for n in search_notes(db, owner_id, q, project_id=None, k=3):
            notes.setdefault(n["note_id"], n)
        if use_openalex:
            try:
                recs, _ = clients.openalex_search(q, per_page=oa_k, year_from=year_from)
                for r in recs:
                    if not r.title:
                        continue
                    p = upsert_publication(db, owner_id, r)
                    pubs.setdefault(str(p.id), publication_to_dict(p))
            except ScholarlyError as e:
                warnings.append(f"OpenAlex search failed for '{q}': {e}")
    db.flush()
    return {"kb": list(kb.values()), "publications": list(pubs.values()), "notes": list(notes.values()), "warnings": warnings}


def note_items(notes: list[dict]) -> list[ContextItem]:
    return [ContextItem("note", f"RESEARCHER NOTE ({n['knowledge_category'].replace('_', ' ')}; {n['note_type']}) — {n['title'][:100]}",
                        n["body"], private=True, ref={"type": "note", "note_id": n["note_id"]}) for n in notes]


def all_items(db, owner_id, project_id, gathered: dict) -> list[ContextItem]:
    return (project_foundation(db, owner_id, project_id) + note_items(gathered["notes"][:6]) + kb_items(gathered["kb"][:14])
            + publication_items(gathered["publications"][:18]))


class WFState(TypedDict, total=False):
    db: Any
    owner_id: uuid.UUID
    project_id: uuid.UUID | None
    job_id: uuid.UUID | None
    input: dict
    frame: dict
    gathered: dict
    items: list
    synthesis: dict
    result: dict
    review: dict
    sources: list
    runs: list


def _agent(state: WFState, agent: str, task: str, instruction: str, items=None, **kw):
    out = run_agent(state["db"], state["owner_id"], agent, task, instruction, context=items if items is not None else state.get("items"),
                    project_id=state.get("project_id"), job_id=state.get("job_id"), json_mode=True, **kw)
    state.setdefault("runs", []).append(str(out.run_id))
    if out.sources:
        state["sources"] = out.sources
    return out.data if isinstance(out.data, dict) else {"items": out.data}


# ---------------------------------------------------------------------------
# 1. Research idea assessment
# ---------------------------------------------------------------------------
def _idea_frame(state: WFState) -> WFState:
    inp = state["input"]
    data = _agent(state, "strategist", "idea_frame",
        f"The researcher describes an idea:\n\"\"\"{inp['statement']}\"\"\"\nAdditional context: {inp.get('context') or 'none'}\n\n"
        "Interrogate it before anything else. Return JSON with keys: central_problem (string), restated_idea (string), "
        "assumptions (list of strings — explicit and hidden), disciplines (list), key_concepts (list), "
        "clarifying_questions (list of questions you would ask the researcher), search_queries (3-5 concise scholarly search queries).",
        items=project_foundation(state["db"], state["owner_id"], state.get("project_id")))
    state["frame"] = data
    return state


def _idea_gather(state: WFState) -> WFState:
    qs = state["frame"].get("search_queries") or [state["input"]["statement"][:200]]
    g = gather_sources(state["db"], state["owner_id"], qs, project_id=state.get("project_id"),
                       use_openalex=state["input"].get("use_openalex", True))
    state["gathered"] = g
    state["items"] = all_items(state["db"], state["owner_id"], state.get("project_id"), g)
    return state


def _idea_synthesise(state: WFState) -> WFState:
    state["synthesis"] = _agent(state, "literature", "idea_synthesis",
        f"Research idea: {state['frame'].get('restated_idea') or state['input']['statement']}\n"
        "Using ONLY the context sources, return JSON with keys: related_research (list of {summary, sources:[S-ids]}), "
        "theoretical_perspectives (list of {perspective, how_it_applies, sources}), "
        "contradictory_or_debated_findings (list of {issue, sources}), coverage_assessment (string: how complete this retrieval is likely to be and what is missing).")
    return state


def _idea_gaps(state: WFState) -> WFState:
    state["result"] = _agent(state, "strategist", "gap_assessment",
        f"Research idea: {state['frame'].get('restated_idea') or state['input']['statement']}\n"
        f"Literature synthesis (from the Literature Researcher): {state['synthesis']}\n\n"
        "Assess research gaps across the dimensions empirical, theoretical, methodological, contextual, conceptual, "
        "contradictory_findings, emerging_problem. Return JSON keys: gaps (list of {type, description, status: 'established'|'potential', "
        "evidence:[S-ids], confidence:'low'|'medium'|'high', what_would_confirm}), originality_assessment (string, cautious), "
        "relevance_assessment (string), possible_contributions (list), suggested_refinements (list), "
        "candidate_research_questions (list — proposals for the researcher to approve or reject).")
    return state


def _idea_critique(state: WFState) -> WFState:
    state["review"] = _agent(state, "reviewer", "assessment_critique",
        f"Critically review this research idea assessment produced by other agents:\n{state['result']}\n\n"
        "Return JSON keys: weaknesses (list), competing_explanations (list), overstated_claims (list), "
        "search_limitations (list), recommendation_for_researcher (string).")
    return state


def build_idea_graph():
    g = StateGraph(WFState)
    for name, fn in (("frame", _idea_frame), ("gather", _idea_gather), ("synthesise", _idea_synthesise),
                     ("gaps", _idea_gaps), ("critique", _idea_critique)):
        g.add_node(name, fn)
    g.set_entry_point("frame")
    g.add_edge("frame", "gather")
    g.add_edge("gather", "synthesise")
    g.add_edge("synthesise", "gaps")
    g.add_edge("gaps", "critique")
    g.add_edge("critique", END)
    return g.compile()


def run_idea_assessment(db, owner_id, statement, context="", project_id=None, job_id=None, use_openalex=True) -> dict:
    st = build_idea_graph().invoke({"db": db, "owner_id": owner_id, "project_id": project_id, "job_id": job_id,
                                    "input": {"statement": statement, "context": context, "use_openalex": use_openalex}})
    return {"frame": st["frame"], "synthesis": st["synthesis"], "assessment": st["result"], "critical_review": st["review"],
            "sources": st.get("sources", []), "retrieval_warnings": st["gathered"]["warnings"],
            "retrieval_counts": {k: len(st["gathered"][k]) for k in ("kb", "publications", "notes")},
            "agent_runs": st.get("runs", []), "knowledge_category": "ai_interpretation"}


# ---------------------------------------------------------------------------
# 2. Research Challenge Mode
# ---------------------------------------------------------------------------
def _ch_queries(state: WFState) -> WFState:
    state["frame"] = _agent(state, "reviewer", "challenge_queries",
        f"The researcher's argument:\n\"\"\"{state['input']['argument']}\"\"\"\n"
        "Return JSON: {claims: [the distinct claims made], causal_assumptions: [...], "
        "counter_queries: [3-5 scholarly search queries likely to surface CONTRADICTORY evidence or competing explanations]}",
        items=project_foundation(state["db"], state["owner_id"], state.get("project_id")))
    return state


def _ch_gather(state: WFState) -> WFState:
    g = gather_sources(state["db"], state["owner_id"], state["frame"].get("counter_queries") or [state["input"]["argument"][:200]],
                       project_id=state.get("project_id"), use_openalex=state["input"].get("use_openalex", True))
    state["gathered"] = g
    state["items"] = all_items(state["db"], state["owner_id"], state.get("project_id"), g)
    return state


def _ch_challenge(state: WFState) -> WFState:
    state["result"] = _agent(state, "reviewer", "challenge",
        f"Argument under challenge:\n\"\"\"{state['input']['argument']}\"\"\"\nIdentified claims: {state['frame'].get('claims')}\n\n"
        "Deliberately challenge it. Return JSON keys (each a list of {point, sources:[S-ids], severity:'minor'|'moderate'|'major'}): "
        "contradictory_evidence, competing_theoretical_explanations, causal_assumption_challenges, research_design_weaknesses, "
        "alternative_interpretations, unsupported_conclusions, methodological_limitations, originality_questions, "
        "confirmation_bias_risks; plus overall_assessment (string) and questions_for_the_researcher (list).", temperature=0.4)
    return state


def build_challenge_graph():
    g = StateGraph(WFState)
    g.add_node("queries", _ch_queries)
    g.add_node("gather", _ch_gather)
    g.add_node("challenge", _ch_challenge)
    g.set_entry_point("queries")
    g.add_edge("queries", "gather")
    g.add_edge("gather", "challenge")
    g.add_edge("challenge", END)
    return g.compile()


def run_challenge(db, owner_id, argument, project_id=None, job_id=None, use_openalex=True) -> dict:
    st = build_challenge_graph().invoke({"db": db, "owner_id": owner_id, "project_id": project_id, "job_id": job_id,
                                         "input": {"argument": argument, "use_openalex": use_openalex}})
    return {"analysis": st["frame"], "challenge": st["result"], "sources": st.get("sources", []),
            "retrieval_warnings": st["gathered"]["warnings"], "agent_runs": st.get("runs", []), "knowledge_category": "ai_interpretation"}


# ---------------------------------------------------------------------------
# 3. Theoretical analysis & 4. Methodology advice
# ---------------------------------------------------------------------------
def run_theory_analysis(db, owner_id, problem, project_id=None, job_id=None, candidate_theories=None, use_openalex=True) -> dict:
    qs = [f"{problem[:150]} theory", f"{problem[:150]} theoretical framework"] + [f"{t} theory" for t in (candidate_theories or [])[:3]]
    g = gather_sources(db, owner_id, qs, project_id=project_id, use_openalex=use_openalex)
    items = all_items(db, owner_id, project_id, g)
    out = run_agent(db, owner_id, "theorist", "theory_analysis",
        f"Research problem: {problem}\nTheories the researcher wants considered: {candidate_theories or 'none specified'}\n\n"
        "Identify potentially relevant theories and evaluate their fit for THIS problem. Return JSON keys: "
        "theories (list of {name, origins, central_assumptions:[...], constructs:[{name, definition}], strengths:[...], limitations:[...], "
        "suitability (why it fits or does not fit this specific problem), sources:[S-ids]}), comparison (string), "
        "defensible_combinations (list of {theories:[...], rationale, risks}), "
        "conceptual_model ({nodes:[{id, label, type:'construct'|'moderator'|'mediator'|'outcome'|'control'}], edges:[{source, target, label, hypothesis_id}]}), "
        "hypotheses (list of {id, statement, rationale, sources}), cautions (list).",
        context=items, project_id=project_id, job_id=job_id, json_mode=True, max_tokens=8000)
    return {"analysis": out.data, "sources": out.sources, "retrieval_warnings": g["warnings"], "agent_run": str(out.run_id),
            "knowledge_category": "ai_interpretation"}


def run_method_advice(db, owner_id, problem, project_id=None, job_id=None, preferences="", use_openalex=False) -> dict:
    g = gather_sources(db, owner_id, [f"{problem[:150]} methodology", f"{problem[:150]} research design"], project_id=project_id,
                       use_openalex=use_openalex)
    items = all_items(db, owner_id, project_id, g)
    out = run_agent(db, owner_id, "methodologist", "method_advice",
        f"Research problem / questions: {problem}\nResearcher preferences or constraints: {preferences or 'none stated'}\n\n"
        "Recommend and JUSTIFY a research design. Return JSON keys: recommended ({approach, design, population, sampling, sample_size "
        "(with the reasoning, e.g. power analysis assumptions — no invented numbers presented as facts), variables:[{name, role, "
        "operational_definition, measure}], instruments, data_collection, reliability_validity, ethics, analysis_plan, rationale}), "
        "alternatives (list of {approach, design, trade_offs}), risks (list), what_the_researcher_must_decide (list).",
        context=items, project_id=project_id, job_id=job_id, json_mode=True, max_tokens=7000)
    return {"advice": out.data, "sources": out.sources, "agent_run": str(out.run_id), "knowledge_category": "ai_interpretation"}


# ---------------------------------------------------------------------------
# 5. Evidence extraction
# ---------------------------------------------------------------------------
EVIDENCE_FIELDS = ["research_objectives", "research_questions", "theoretical_framework", "methodology", "sample_and_context",
                   "principal_findings", "limitations", "implications", "research_gaps"]


def run_evidence_extraction(db, owner_id, items: list[ContextItem], basis: str, job_id=None) -> dict:
    out = run_agent(db, owner_id, "literature", "evidence_extraction",
        f"Extract evidence from this single publication ({basis}). For each field in {EVIDENCE_FIELDS} return "
        "{value: string or null if not stated, sources: [S-ids where it is stated], quote: short verbatim supporting quote or null}. "
        "Use null rather than guessing. Return JSON with exactly those field keys.",
        context=items, job_id=job_id, json_mode=True, temperature=0.1)
    return {"fields": out.data, "sources": out.sources, "provider": out.provider, "model": out.model, "agent_run": str(out.run_id)}


# ---------------------------------------------------------------------------
# 6. Workspace conversation
# ---------------------------------------------------------------------------
def workspace_reply(db, owner_id, agent: str, message: str, history, project_id=None, use_openalex=False) -> dict:
    g = gather_sources(db, owner_id, [message[:300]], project_id=project_id, use_openalex=use_openalex, kb_k=8, oa_k=5)
    items = all_items(db, owner_id, project_id, g)
    out = run_agent(db, owner_id, agent, "workspace_chat",
        f"Researcher's message:\n{message}\n\nRespond helpfully as your role. Cite context sources inline as [S#]. "
        "Label any of your own interpretations as such. If the researcher states an opinion, treat it as provisional unless they say it is established.",
        context=items, project_id=project_id, history=history, temperature=0.4, max_tokens=3000)
    return {"text": out.text, "sources": out.sources, "agent_run": str(out.run_id), "excluded_private": out.excluded_private,
            "warnings": g["warnings"]}
