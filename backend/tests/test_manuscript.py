import io
import re
from collections import Counter

import docx
import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.services.llm import router
from tests.conftest import ScriptedLLM, run_jobs


def _setup(c, mode="complete", words=7000):
    pid = c.post("/api/projects", json={"title": "Digital trust", "description": "Institutional capacity and digital trust"}).json()["id"]
    for i, (fam, yr, title) in enumerate([("North", 1990, "Institutions, institutional change and economic performance"),
                                          ("Khanna", 1997, "Why focused strategies may be wrong for emerging markets")]):
        c.post("/api/literature/references", json={"project_id": pid, "csl": {"title": title, "authors": [{"family": fam, "given": "D."}],
                                                                               "year": yr, "venue": "Test Venue"}})
    m = c.post("/api/manuscripts", json={"project_id": pid, "title": "Institutional capacity and digital trust adoption", "mode": mode,
                                         "target_word_count": words, "citation_style": "apa7",
                                         "requirements": {"objectives": "Explain adoption", "research_questions": "RQ1"}}).json()
    return pid, m


def written_sections(llm) -> Counter:
    return Counter(re.search(r"NOW WRITE: section '([^']+)'", c["prompt"]).group(1) for c in llm.calls if "NOW WRITE: section" in c["prompt"])


def test_word_allocation_7000(auth_client):
    _, m = _setup(auth_client)
    secs = {s["key"]: s["target_words"] for s in m["sections"]}
    assert secs == {"abstract": 250, "introduction": 1000, "literature_review": 2000, "methodology": 1000, "results": 1000,
                    "discussion": 1250, "conclusion": 500}
    assert sum(v for k, v in secs.items() if k != "abstract") == 6750
    s = auth_client.get("/api/manuscripts/structures?target_word_count=10000").json()
    # as in the brief's 7,000-word table, the abstract is counted inside the target total
    assert sum(x["target_words"] for x in s["empirical_article"]) == 10000
    r = auth_client.post(f"/api/manuscripts/{m['id']}/allocation", json={"target_word_count": 5000}).json()
    assert abs(r["sections"][1]["target_words"] - 741) < 20


def test_complete_generation_audit_and_export(auth_client, llm):
    c = auth_client
    pid, m = _setup(c)
    j = c.post(f"/api/manuscripts/{m['id']}/generate", json={}).json()["job_id"]
    run_jobs()
    job = c.get(f"/api/jobs/{j}").json()
    assert job["status"] == "succeeded", job["error"]
    assert [s["status"] for s in job["steps"]] == ["succeeded"] * len(job["steps"])
    full = c.get(f"/api/manuscripts/{m['id']}").json()
    assert full["status"] == "draft_complete"
    secs = {s["key"]: s for s in full["sections"]}
    # every section within ±15% of its allocation
    for s in full["sections"]:
        assert abs(s["word_count"] - s["target_words"]) <= 0.15 * s["target_words"], (s["key"], s["word_count"])
    assert 6000 <= full["total_words"] <= 7500
    # long sections written subsection by subsection (no single giant response)
    assert written_sections(llm)["Literature Review and Theoretical Framework"] == 2
    # no fabricated results without data
    assert "[DATA REQUIRED" in secs["results"]["content"] and secs["results"]["outstanding_requirements"]
    assert "NO EMPIRICAL DATA" in next(x["prompt"] for x in llm.calls if "NOW WRITE: section 'Results'" in x["prompt"])
    # citations only from the project's reference list
    refs = c.get(f"/api/literature/references?project_id={pid}").json()
    keys = {r["citekey"] for r in refs}
    used = set(re.findall(r"\[@([\w:\-]+)", " ".join(s["content"] for s in full["sections"])))
    assert used and used <= keys
    qr = full["quality_report"]
    assert {"consistency", "citation_audit", "word_allocation", "critical_review"} <= set(qr)
    types = {f["type"] for f in qr["citation_audit"]["findings"]}
    assert "unverified_reference" in types  # refs were never verified -> flagged
    assert "not a formal plagiarism check" in qr["citation_audit"]["note"]
    assert c.get(f"/api/manuscripts/{m['id']}/versions").json()[0]["created_by"] == "ai"
    # editable DOCX with rendered citations and reference list
    r = c.get(f"/api/manuscripts/{m['id']}/export/docx")
    assert r.status_code == 200
    d = docx.Document(io.BytesIO(r.content))
    text_all = "\n".join(p.text for p in d.paragraphs)
    assert "References" in text_all and "North, D. (1990)" in text_all
    assert "(North, 1990)" in text_all or "(Khanna, 1997)" in text_all
    assert "[@" not in text_all
    assert c.get(f"/api/manuscripts/{m['id']}/export/tex").text.startswith("\\documentclass")


def test_interrupt_by_researcher_and_resume_without_regeneration(auth_client):
    c = auth_client
    _, m = _setup(c)

    class PausingLLM(ScriptedLLM):
        def generate(self, messages, **kw):
            res = super().generate(messages, **kw)
            if len(written_sections(self)) == 3 and not getattr(self, "paused", False):
                self.paused = True
                db = SessionLocal()
                db.execute(text("UPDATE generation_jobs SET status='cancel_requested' WHERE job_type='manuscript_generate'"))
                db.commit()
                db.close()
            return res

    llm = PausingLLM()
    router.set_provider_override(lambda: [llm])
    try:
        j = c.post(f"/api/manuscripts/{m['id']}/generate", json={}).json()["job_id"]
        run_jobs()
        job = c.get(f"/api/jobs/{j}").json()
        assert job["status"] == "paused"
        done = [s["key"] for s in job["steps"] if s["status"] == "succeeded"]
        assert "outline" in done and len([d for d in done if d.startswith("write:")]) == 3
        assert c.post(f"/api/manuscripts/{m['id']}/generate", json={}).status_code == 409  # no duplicate job
        c.post(f"/api/jobs/{j}/resume")
        run_jobs()
        job = c.get(f"/api/jobs/{j}").json()
        assert job["status"] == "succeeded"
        counts = written_sections(llm)
        # each section generated exactly once across the interruption (lit review = 2 subsections)
        assert counts == {"Introduction": 1, "Literature Review and Theoretical Framework": 2, "Methodology": 1, "Results": 1,
                          "Discussion": 2, "Conclusion and Implications": 1, "Abstract": 1}
        assert sum(1 for x in llm.calls if "Develop the manuscript outline" in x["prompt"]) == 1
    finally:
        router.set_provider_override(None)


def test_worker_crash_recovery(auth_client):
    c = auth_client
    _, m = _setup(c, words=3000)

    class CrashingLLM(ScriptedLLM):
        def generate(self, messages, **kw):
            if len(written_sections(self)) == 2 and not getattr(self, "crashed", False) and "NOW WRITE" in messages[-1].content:
                self.crashed = True
                raise RuntimeError("simulated worker crash")
            return super().generate(messages, **kw)

    llm = CrashingLLM()
    router.set_provider_override(lambda: [llm])
    try:
        j = c.post(f"/api/manuscripts/{m['id']}/generate", json={}).json()["job_id"]
        run_jobs()
        job = c.get(f"/api/jobs/{j}").json()
        assert job["status"] == "queued" and "simulated worker crash" in job["error"]  # retried with backoff
        db = SessionLocal()
        db.execute(text("UPDATE generation_jobs SET run_after = now() WHERE id = :id"), {"id": j})
        db.commit()
        db.close()
        run_jobs()
        assert c.get(f"/api/jobs/{j}").json()["status"] == "succeeded"
        counts = written_sections(llm)
        assert counts["Introduction"] == 1  # completed before the crash, not regenerated
    finally:
        router.set_provider_override(None)


def test_expired_lease_is_recovered(auth_client, llm):
    """A job left 'running' by a dead worker is picked up once its lease expires."""
    c = auth_client
    _, m = _setup(c, words=3000)
    j = c.post(f"/api/manuscripts/{m['id']}/outline").json()["job_id"]
    db = SessionLocal()
    db.execute(text("UPDATE generation_jobs SET status='running', locked_by='dead-worker', locked_until=now() - interval '1 minute' WHERE id=:id"), {"id": j})
    db.commit()
    db.close()
    run_jobs()
    assert c.get(f"/api/jobs/{j}").json()["status"] == "succeeded"


def test_guided_mode_section_workflow(auth_client, llm):
    c = auth_client
    pid, m = _setup(c, mode="guided", words=5000)
    c.post(f"/api/manuscripts/{m['id']}/outline")
    run_jobs()
    full = c.get(f"/api/manuscripts/{m['id']}").json()
    assert full["status"] == "outlined" and all(s["objective"] for s in full["sections"])
    r = c.post(f"/api/manuscripts/{m['id']}/sections/literature_review/generate").json()
    assert "earlier sections not yet approved" in r["warning"]
    c.post(f"/api/manuscripts/{m['id']}/sections/introduction/generate")
    run_jobs()
    sec = next(s for s in c.get(f"/api/manuscripts/{m['id']}").json()["sections"] if s["key"] == "introduction")
    assert sec["status"] == "draft" and sec["word_count"] > 0
    # researcher edits directly, then approves
    edited = sec["content"] + "\n\nThis is my own sentence citing [@ghost2020]."
    s2 = c.put(f"/api/manuscripts/{m['id']}/sections/introduction", json={"content": edited}).json()
    assert s2["origin"] == "mixed"
    d = c.post(f"/api/manuscripts/{m['id']}/sections/introduction/decision", json={"decision": "approved"}).json()
    assert d["section"]["status"] == "approved"
    # deterministic audit flags the nonexistent citation
    rep = c.post(f"/api/manuscripts/{m['id']}/audit?alignment=false").json()["report"]
    crit = [f for f in rep["findings"] if f["type"] == "nonexistent_reference"]
    assert crit and crit[0]["citekey"] == "ghost2020"
    render = c.get(f"/api/manuscripts/{m['id']}/render").json()
    assert "ghost2020" in render["unresolved"]
    assert "UNVERIFIED CITATION: ghost2020" in render["sections"][1]["content"]
    # instruction-driven regeneration
    r = c.post(f"/api/manuscripts/{m['id']}/sections/introduction/decision", json={"decision": "instruction", "instructions": "Shorter"}).json()
    assert r["job_id"]
    run_jobs()
    assert "RESEARCHER INSTRUCTIONS: Shorter" in llm.calls[-1]["prompt"]
    assert any(x["decision_type"] == "manuscript_section" for x in c.get(f"/api/projects/{pid}/decisions").json())


def test_versions_compare_and_restore(auth_client, llm):
    c = auth_client
    _, m = _setup(c, mode="guided", words=3000)
    c.put(f"/api/manuscripts/{m['id']}/sections/introduction", json={"content": "First draft of the introduction."})
    v1 = c.post(f"/api/manuscripts/{m['id']}/versions?label=v1").json()
    c.put(f"/api/manuscripts/{m['id']}/sections/introduction", json={"content": "Second draft, substantially rewritten."})
    cmp = c.get(f"/api/manuscripts/{m['id']}/compare?a={v1['id']}&b=current").json()
    ch = next(s for s in cmp["sections"] if s["key"] == "introduction")
    assert ch["changed"] and any("Second draft" in line for line in ch["diff"])
    c.post(f"/api/manuscripts/{m['id']}/versions/{v1['id']}/restore")
    sec = next(s for s in c.get(f"/api/manuscripts/{m['id']}").json()["sections"] if s["key"] == "introduction")
    assert sec["content"] == "First draft of the introduction."
    assert len(c.get(f"/api/manuscripts/{m['id']}/versions").json()) == 2  # auto-snapshot before restore


def test_pause_after_outline_requires_approval(auth_client, llm):
    c = auth_client
    _, m = _setup(c, words=3000)
    j = c.post(f"/api/manuscripts/{m['id']}/generate", json={"pause_after_outline": True}).json()["job_id"]
    run_jobs()
    assert c.get(f"/api/jobs/{j}").json()["status"] == "awaiting_approval"
    assert written_sections(llm) == Counter()
    c.post(f"/api/jobs/{j}/resume")
    run_jobs()
    assert c.get(f"/api/jobs/{j}").json()["status"] == "succeeded"


@pytest.mark.parametrize("style,expect", [("apa7", "(North, 1990)"), ("harvard", "(North, 1990)"), ("ieee", "[1]"), ("chicago_ad", "(North 1990)")])
def test_citation_styles(style, expect):
    from app.services import citations as cit

    refs = {"north1990": {"title": "Institutions", "authors": [{"family": "North", "given": "Douglass C."}], "year": 1990,
                          "publisher": "Cambridge University Press"}}
    out = cit.render_intext("Institutions matter [@north1990].", refs, style, {"north1990": 1})
    assert expect in out
    bib = cit.build_bibliography(["north1990"], refs, style)
    assert "North" in bib[0] and "1990" in bib[0]
