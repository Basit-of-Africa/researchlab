"""Test fixtures. Tests run against a real PostgreSQL + pgvector database
(TEST_DATABASE_URL) with the real Alembic migrations applied.

The only test double is the LLM: a deterministic in-process provider
(``ScriptedLLM``) registered via the router override hook, because CI has no
model credentials. All other integrations are real.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://researchlab:researchlab@localhost:5432/researchlab_test")
_STORAGE = tempfile.mkdtemp(prefix="rl-test-storage-")
os.environ.update({
    "DATABASE_URL": TEST_DB, "ENVIRONMENT": "test", "LOCAL_STORAGE_PATH": _STORAGE, "EMBEDDING_PROVIDER": "hashing",
    "SECRET_KEY": "test-secret-key-test-secret-key-0123456789", "RATE_LIMIT_PER_MINUTE": "100000", "LOGIN_RATE_LIMIT_PER_MINUTE": "1000",
    "LLM_PROVIDERS": "gemini", "GEMINI_API_KEY": "", "RUN_EMBEDDED_WORKER": "false",
})

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db import reset_engine  # noqa: E402
from app.services.llm import router as llm_router  # noqa: E402
from app.services.llm.base import LLMResult  # noqa: E402
from app.services.storage import reset_storage  # noqa: E402


class ScriptedLLM:
    """Deterministic test double that answers according to the task in the prompt."""

    name = "scripted-test"
    model = "scripted-v1"

    def __init__(self, external: bool = False) -> None:
        self.external = external
        self.calls: list[dict] = []

    def available(self) -> bool:
        return True

    def generate(self, messages, system="", temperature=0.3, max_tokens=4096, json_mode=False) -> LLMResult:
        prompt = messages[-1].content
        self.calls.append({"system": system, "prompt": prompt, "json": json_mode})
        sids = re.findall(r"^\[(S\d+)\]", prompt, re.M)
        keys = re.findall(r"cite as \[@([\w:\-]+)\]", prompt)
        if json_mode:
            return LLMResult(json.dumps(self._json(prompt, sids)), self.name, self.model, 10, 10)
        m = re.search(r"approximately (\d+) words", prompt)
        n = int(m.group(1)) if m else 120
        sub = re.search(r"Begin with the subsection heading as '## (.+?)'", prompt)
        words = []
        cite = f" [@{keys[0]}]" if keys else " [CITATION NEEDED: supporting literature]"
        sentence = "Institutional capacity shapes how digital trust infrastructure is adopted by enterprises in emerging economies"
        while len(words) < n:
            words.extend((sentence + (cite if len(words) % 60 == 0 else "") + ".").split())
        body = " ".join(words[:n])
        if "NO EMPIRICAL DATA" in prompt:
            body = "The results will report the measurement model and structural estimates. [DATA REQUIRED: survey responses from SMEs]. " + body
        text_out = (f"## {sub.group(1)}\n\n" if sub else "") + body
        return LLMResult(text_out, self.name, self.model, 10, n)

    def _json(self, prompt: str, sids: list[str]) -> dict:
        s = sids[:2]
        if "Interrogate it before anything else" in prompt:
            return {"central_problem": "How institutional voids shape digital trust adoption", "restated_idea": "Digital trust in African SMEs",
                    "assumptions": ["Trust is measurable"], "disciplines": ["Information systems"], "key_concepts": ["digital trust"],
                    "clarifying_questions": ["Which sector?"], "search_queries": ["digital trust infrastructure", "institutional voids"]}
        if "Critically review this research idea" in prompt:
            return {"weaknesses": ["Broad"], "competing_explanations": ["Cost"], "overstated_claims": [], "search_limitations": ["Small sample"],
                    "recommendation_for_researcher": "Narrow scope"}
        if "Assess research gaps" in prompt:
            return {"gaps": [{"type": "contextual", "description": "Few African SME studies", "status": "potential", "evidence": s,
                              "confidence": "low", "what_would_confirm": "Systematic search"}], "originality_assessment": "Uncertain",
                    "relevance_assessment": "Relevant", "possible_contributions": ["Context"], "suggested_refinements": ["Narrow sector"],
                    "candidate_research_questions": ["How do institutional voids affect digital trust adoption?"]}
        if "related_research" in prompt:
            return {"related_research": [{"summary": "Prior work links institutions and trust", "sources": s}], "theoretical_perspectives": [],
                    "contradictory_or_debated_findings": [], "coverage_assessment": "Limited retrieval"}
        if "counter_queries" in prompt:
            return {"claims": ["Trust drives adoption"], "causal_assumptions": ["Direction"], "counter_queries": ["trust adoption null effect"]}
        if "Deliberately challenge" in prompt:
            return {"contradictory_evidence": [{"point": "Mixed findings", "sources": s, "severity": "moderate"}], "competing_theoretical_explanations": [],
                    "causal_assumption_challenges": [], "research_design_weaknesses": [], "alternative_interpretations": [], "unsupported_conclusions": [],
                    "methodological_limitations": [], "originality_questions": [], "confirmation_bias_risks": [{"point": "Selective sources", "sources": [], "severity": "minor"}],
                    "overall_assessment": "Plausible but contested", "questions_for_the_researcher": ["What would falsify this?"]}
        if "Identify potentially relevant theories" in prompt:
            return {"theories": [{"name": "Institutional Theory", "origins": "Sociology", "central_assumptions": ["Isomorphism"],
                                  "constructs": [{"name": "Coercive pressure", "definition": "Regulation"}], "strengths": ["Context"], "limitations": ["Agency"],
                                  "suitability": "Fits institutional voids", "sources": s}], "comparison": "Compared",
                    "defensible_combinations": [], "conceptual_model": {"nodes": [{"id": "a", "label": "Institutional quality", "type": "construct"},
                                                                                   {"id": "b", "label": "Digital trust", "type": "outcome"}],
                                                                         "edges": [{"source": "a", "target": "b", "label": "+", "hypothesis_id": "H1"}]},
                    "hypotheses": [{"id": "H1", "statement": "Institutional quality increases digital trust", "rationale": "Theory", "sources": s}], "cautions": []}
        if "Recommend and JUSTIFY" in prompt:
            return {"recommended": {"approach": "quantitative", "design": "Cross-sectional survey", "population": "SMEs", "sampling": "Stratified",
                                    "sample_size": "Power analysis required", "variables": [{"name": "Trust", "role": "DV"}], "instruments": "Validated scales",
                                    "data_collection": "Online survey", "reliability_validity": "CFA", "ethics": "Consent", "analysis_plan": "PLS-SEM",
                                    "rationale": "Fits RQs"}, "alternatives": [], "risks": [], "what_the_researcher_must_decide": ["Sector"]}
        if "Develop the manuscript outline" in prompt:
            secs = re.findall(r"- key=([\w_]+); heading=(.+?); target_words=(\d+)", prompt)
            out = []
            for k, h, w in secs:
                w = int(w)
                subs = [] if k == "abstract" else [{"heading": f"{h} part {i + 1}", "points": ["point"], "target_words": w // 2 if i == 0 else w - w // 2} for i in range(2)]
                out.append({"key": k, "objective": f"Objective of {h}", "subsections": subs, "evidence_needed": ["literature"], "outstanding_requirements": []})
            return {"central_argument": "Institutions condition digital trust", "key_terms": [{"term": "digital trust", "definition": "confidence in digital systems"}], "sections": out}
        if "cross-section consistency" in prompt:
            return {"terminology_inconsistencies": [], "argument_progression": "Coherent", "contradictions": [], "repetition": ["Repetitive sentences"],
                    "transition_problems": [], "research_question_alignment": "Aligned"}
        if "does the source support the claim" in prompt:
            return {"verdict": "cannot_determine", "explanation": "Abstract does not address the claim", "supporting_quote": None}
        if "rigorous peer reviewer" in prompt:
            return {"overall_assessment": "Draft", "contribution_assessment": "Modest", "strengths": [], "major_concerns": [{"issue": "No data", "section": "results", "suggestion": "Collect data"}],
                    "minor_concerns": [], "methodological_concerns": [], "unsupported_claims": [], "questions_for_author": [], "readiness": "not_ready"}
        if "Extract evidence" in prompt:
            return {f: {"value": None, "sources": [], "quote": None} for f in ["research_objectives", "principal_findings"]} | {
                "methodology": {"value": "Survey", "sources": s[:1], "quote": None}}
        if "Propose inductive codes" in prompt:
            return {"codes": [{"name": "Trust barriers", "description": "Obstacles", "theme": "Barriers", "segment_sids": s[:1]}]}
        if "WRITING STYLE" in prompt:
            return {"summary": "Formal", "traits": ["Uses hedging"]}
        return {}


@pytest.fixture(scope="session")
def _migrated():
    eng = create_engine(TEST_DB)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    eng.dispose()
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    yield
    shutil.rmtree(_STORAGE, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_db(_migrated):
    eng = create_engine(TEST_DB)
    with eng.begin() as c:
        tables = [r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
        c.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))
    eng.dispose()
    reset_engine()
    reset_storage()
    from app.security import rate_limiter

    rate_limiter.reset()
    yield


@pytest.fixture
def llm():
    p = ScriptedLLM()
    llm_router.set_provider_override(lambda: [p])
    yield p
    llm_router.set_provider_override(None)


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


PASSWORD = "correct horse battery staple"


@pytest.fixture
def auth_client(client):
    r = client.post("/api/auth/register", json={"email": "researcher@example.org", "password": PASSWORD, "display_name": "Test Researcher"})
    assert r.status_code == 200, r.text
    return client


def run_jobs() -> int:
    from app.services.jobs import run_pending_jobs

    return run_pending_jobs()


def network_ok(host: str = "https://api.openalex.org/works?per-page=1") -> bool:
    import httpx

    try:
        return httpx.get(host, timeout=10).status_code == 200
    except Exception:  # noqa: BLE001
        return False
