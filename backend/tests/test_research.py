from tests.conftest import ScriptedLLM, run_jobs
from tests.helpers import make_pdf


def _project(c):
    return c.post("/api/projects", json={"title": "Digital trust in African SMEs", "description": "Institutional capacity and digital trust",
                                         "objectives": ["Explain adoption"]}).json()["id"]


def _ingest(c, **form):
    r = c.post("/api/knowledge/documents/upload", files={"file": ("p.pdf", make_pdf(), "application/pdf")}, data={"verify_metadata": "false", **form})
    run_jobs()
    return r.json()["document"]["id"]


def test_idea_assessment_workflow_and_researcher_decisions(auth_client, llm):
    c = auth_client
    pid = _project(c)
    _ingest(c, visibility="published")
    r = c.post("/api/research/ideas", json={"statement": "Institutional voids slow the adoption of digital trust services by African SMEs",
                                            "project_id": pid, "use_openalex": False}).json()
    run_jobs()
    assert c.get(f"/api/jobs/{r['job_id']}").json()["status"] == "succeeded"
    idea = c.get(f"/api/research/ideas/{r['idea']['id']}").json()
    a = idea["assessment"]
    # all four agents ran in sequence via the LangGraph workflow
    assert set(a) >= {"frame", "synthesis", "assessment", "critical_review", "sources"}
    assert a["knowledge_category"] == "ai_interpretation"
    gap = a["assessment"]["gaps"][0]
    assert gap["status"] == "potential"  # never auto-declared established
    assert a["sources"], "sources must be traceable"
    assert a["retrieval_counts"]["kb"] >= 1
    # AI must not approve anything by itself
    assert c.get(f"/api/projects/{pid}").json()["questions"] == []
    q = c.post(f"/api/projects/{pid}/questions", json={"text": a["assessment"]["candidate_research_questions"][0], "origin": "ai"}).json()
    assert q["status"] == "proposed"
    d = c.post(f"/api/projects/{pid}/approvals", json={"entity_type": "research_question", "entity_id": q["id"], "decision": "approved",
                                                       "rationale": "Fits the project"}).json()
    assert d["decision"] == "approved"
    assert c.get(f"/api/projects/{pid}").json()["questions"][0]["status"] == "approved"
    c.post(f"/api/projects/{pid}/approvals", json={"entity_type": "gap", "decision": "deferred", "subject": gap["description"], "payload": gap})
    decs = c.get(f"/api/projects/{pid}/decisions").json()
    assert [x["decision"] for x in decs] == ["deferred", "approved"]
    # researcher challenges the assessment -> new version linked to the old one
    rv = c.post(f"/api/research/ideas/{idea['id']}/revise", json={"statement": "Focus on Nigerian fintech SMEs", "researcher_response": "Too broad",
                                                                  "reassess": False}).json()
    assert rv["idea"]["parent_id"] == idea["id"] and rv["idea"]["version"] == 2
    hist = c.get(f"/api/projects/{pid}/history").json()
    assert {r["agent"] for r in hist["agent_runs"]} >= {"strategist", "literature", "reviewer"}


def test_integrity_rules_and_source_labels_in_prompts(auth_client, llm):
    c = auth_client
    _ingest(c, visibility="published", knowledge_category="external_evidence")
    c.post("/api/notes", json={"body": "I believe trust precedes adoption", "knowledge_category": "provisional_opinion"})
    r = c.post("/api/knowledge/ask", json={"question": "What do I know about digital trust adoption?"}).json()
    assert r["sources"] and r["knowledge_category"] == "ai_interpretation"
    call = llm.calls[-1]
    assert "Never invent authors" in call["system"]
    assert "EXTERNAL EVIDENCE" in call["prompt"]
    assert "RESEARCHER NOTE (provisional opinion" in call["prompt"]


def test_private_material_withheld_from_external_provider(auth_client):
    from app.services.llm import router

    ext = ScriptedLLM(external=True)
    router.set_provider_override(lambda: [ext])
    try:
        c = auth_client
        _ingest(c)  # default visibility: private_unpublished
        r = c.post("/api/knowledge/ask", json={"question": "institutional capacity digital trust"}).json()
        assert r["excluded_private_sources"] >= 1
        assert "Institutional capacity was positively associated" not in ext.calls[-1]["prompt"]
        assert "withheld" in ext.calls[-1]["prompt"]
        # researcher explicitly opts in
        c.put("/api/settings/ai-data-policy", json={"allow_external_private": True})
        r2 = c.post("/api/knowledge/ask", json={"question": "institutional capacity digital trust"}).json()
        assert r2["excluded_private_sources"] == 0
    finally:
        router.set_provider_override(None)


def test_challenge_mode(auth_client, llm):
    c = auth_client
    pid = _project(c)
    j = c.post("/api/research/challenge", json={"argument": "Digital trust infrastructure always increases SME performance in Africa.",
                                               "project_id": pid, "use_openalex": False}).json()["job_id"]
    run_jobs()
    job = c.get(f"/api/jobs/{j}").json()
    assert job["status"] == "succeeded"
    ch = job["result"]["challenge"]
    for k in ("contradictory_evidence", "confirmation_bias_risks", "questions_for_the_researcher"):
        assert k in ch
    assert any("Deliberately challenge" in x["prompt"] for x in llm.calls)


def test_theory_and_methodology_require_approval(auth_client, llm):
    c = auth_client
    pid = _project(c)
    c.post("/api/research/theory", json={"problem": "How do institutional voids shape digital trust adoption?", "project_id": pid, "use_openalex": False})
    c.post("/api/research/methodology", json={"problem": "How do institutional voids shape digital trust adoption?", "project_id": pid})
    run_jobs()
    p = c.get(f"/api/projects/{pid}").json()
    assert {f["status"] for f in p["frameworks"]} == {"proposed"}
    model = next(f for f in p["frameworks"] if f["model"])
    assert model["model"]["edges"][0]["hypothesis_id"] == "H1"
    assert p["methods"][0]["status"] == "proposed" and p["methods"][0]["origin"] == "ai"
    fw = next(f for f in p["frameworks"] if f["name"] == "Institutional Theory")
    c.post(f"/api/projects/{pid}/approvals", json={"entity_type": "framework", "entity_id": fw["id"], "decision": "approved"})
    c.post(f"/api/projects/{pid}/approvals", json={"entity_type": "method", "entity_id": p["methods"][0]["id"], "decision": "rejected",
                                                   "rationale": "Prefer qualitative"})
    p = c.get(f"/api/projects/{pid}").json()
    approved = next(f for f in p["frameworks"] if f["id"] == fw["id"])
    assert approved["status"] == "approved" and approved["knowledge_category"] == "established_position"
    assert p["methods"][0]["status"] == "rejected"
    # approved foundation is shared with later agents; rejected method is not presented as approved
    c.post("/api/research/methodology", json={"problem": "Revisit design", "project_id": pid})
    run_jobs()
    last = llm.calls[-1]["prompt"]
    assert "RESEARCHER-APPROVED theoretical framework: Institutional Theory" in last
    assert "RESEARCHER-APPROVED methodology" not in last


def test_workspace_conversation_and_promotion(auth_client, llm):
    c = auth_client
    pid = _project(c)
    t = c.post("/api/research/threads", json={"project_id": pid}).json()
    r = c.post(f"/api/research/threads/{t['id']}/messages", json={"content": "Which theories explain trust in mobile money?", "agent": "theorist"}).json()
    assert r["reply"]["role"] == "agent" and r["reply"]["knowledge_category"] == "ai_interpretation"
    # conversation is not knowledge until promoted; AI output stays labelled as AI interpretation
    assert c.get(f"/api/notes?project_id={pid}").json() == []
    n = c.post(f"/api/research/messages/{r['reply']['id']}/promote", json={"knowledge_category": "established_position"}).json()
    assert n["knowledge_category"] == "ai_interpretation"
    n2 = c.post(f"/api/research/messages/{r['researcher_message']['id']}/promote", json={"knowledge_category": "provisional_opinion", "note_type": "question"}).json()
    assert n2["knowledge_category"] == "provisional_opinion"
    assert len(c.get(f"/api/notes?project_id={pid}").json()) == 2


def test_no_provider_gives_clear_error(auth_client):
    r = auth_client.post("/api/knowledge/ask", json={"question": "anything at all?"})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]


def test_project_lifecycle(auth_client):
    c = auth_client
    pid = _project(c)
    c.post(f"/api/projects/{pid}/questions", json={"text": "How does X affect Y?"})
    c.post("/api/notes", json={"body": "note", "project_id": pid})
    dup = c.post(f"/api/projects/{pid}/duplicate").json()
    assert dup["counts"]["questions"] == 1 and dup["counts"]["notes"] == 1
    c.patch(f"/api/projects/{pid}", json={"title": "Renamed"})
    c.post(f"/api/projects/{pid}/archive")
    assert [p["id"] for p in c.get("/api/projects").json()] == [dup["id"]]
    assert c.get(f"/api/projects/{pid}/export").headers["content-type"] == "application/zip"
    assert c.delete(f"/api/projects/{pid}").status_code == 400  # confirmation required
    assert c.delete(f"/api/projects/{pid}?confirm=Renamed").status_code == 200


def test_writing_style_profile(auth_client, llm):
    from tests.helpers import make_docx

    c = auth_client
    paras = ["However, the evidence suggests that institutional capacity may condition how organisations analyse digital risk. "
             "Moreover, we argue that trust is socially constructed (North, 1990). It appears that firms prioritise legitimacy."] * 6
    d = c.post("/api/knowledge/documents/upload", files={"file": ("mine.docx", make_docx(paras), "application/octet-stream")},
               data={"is_own_work": "true"}).json()["document"]
    run_jobs()
    wp = c.get("/api/profile/writing").json()[0]
    c.put(f"/api/profile/writing/{wp['id']}", json={"sample_document_ids": [d["id"]]})
    c.post(f"/api/profile/writing/{wp['id']}/analyse")
    run_jobs()
    wp = c.get("/api/profile/writing").json()[0]
    assert wp["metrics"]["spelling_variant"] == "en-GB"
    assert wp["metrics"]["hedges_per_1000"] > 0
    assert wp["qualitative"]["_label"] == "AI interpretation"
    wp = c.put(f"/api/profile/writing/{wp['id']}", json={"researcher_overrides": {"voice": "Avoid first person plural"}}).json()
    assert wp["researcher_overrides"]["voice"] == "Avoid first person plural"
