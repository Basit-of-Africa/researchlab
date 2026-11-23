from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import DocumentChunk, DocumentEmbedding
from tests.conftest import run_jobs
from tests.helpers import make_pdf


def upload(c, data=None, name="paper.pdf", **form):
    r = c.post("/api/knowledge/documents/upload", files={"file": (name, data or make_pdf(), "application/pdf")},
               data={"verify_metadata": "false", **form})
    assert r.status_code == 200, r.text
    return r.json()


def test_pdf_ingestion_pipeline(auth_client):
    c = auth_client
    up = upload(c)
    assert up["document"]["status"] == "pending"
    assert run_jobs() == 1
    job = c.get(f"/api/jobs/{up['job_id']}").json()
    assert job["status"] == "succeeded", job
    assert job["steps"][0]["status"] == "succeeded"
    d = c.get(f"/api/knowledge/documents/{up['document']['id']}").json()
    assert d["status"] == "ready"
    assert "Institutional Capacity and Digital Trust" in d["title"]
    assert d["doi"] == "10.1234/rl.test.2024.001"
    assert d["metadata_source"]["doi"] == "extracted"
    assert d["metadata_verified"] is False  # extracted, not registry-verified
    assert "institutional capacity" in d["abstract"].lower()
    v = d["versions"][0]
    assert v["page_count"] == 2
    headings = [h["text"] for h in v["headings"]]
    assert "1. Introduction" in headings and "References" in headings
    assert len(d["extracted_references"]) == 2
    assert d["chunk_count"] >= 3
    assert d["embedding_models"] == ["hashing-lexical-v1"]
    chunks = c.get(f"/api/knowledge/documents/{d['id']}/chunks").json()
    assert any("Methodology" in ch["section"] for ch in chunks)
    assert all(ch["page_start"] for ch in chunks)
    assert not any("Khanna" in ch["text"] for ch in chunks)  # references not indexed as content


def test_hybrid_search_with_provenance(auth_client):
    c = auth_client
    up = upload(c)
    run_jobs()
    for mode in ("hybrid", "fulltext", "semantic"):
        r = c.post("/api/knowledge/search", json={"query": "partial least squares structural equation modelling", "mode": mode}).json()
        assert r["results"], mode
        top = r["results"][0]
        assert top["document_id"] == up["document"]["id"]
        assert "Methodology" in top["section"]
        assert top["page_start"] == 1
    r = c.post("/api/knowledge/search", json={"query": "electronic signatures", "year_from": 2100}).json()
    assert r["results"] == []  # metadata filters apply


def test_replace_reindex_preserves_notes_and_reuses_embeddings(auth_client):
    c = auth_client
    up = upload(c)
    run_jobs()
    did = up["document"]["id"]
    note = c.post("/api/notes", json={"body": "Key passage on regulatory confidence", "document_id": did,
                                      "knowledge_category": "provisional_opinion", "anchor": {"page": 2}}).json()
    db = SessionLocal()
    n_emb_v1 = db.scalar(select(func.count(DocumentEmbedding.id)).join(DocumentChunk).where(DocumentChunk.document_id == did))
    db.close()
    r = c.post(f"/api/knowledge/documents/{did}/replace", files={"file": ("paper-v2.pdf", make_pdf(version=2), "application/pdf")},
               data={"change_note": "Revised findings"})
    assert r.status_code == 200
    run_jobs()
    res = c.get(f"/api/jobs/{r.json()['job_id']}").json()["result"]
    assert res["embeddings_reused"] >= 1 and res["embeddings_generated"] >= 1  # only changed chunks re-embedded
    d = c.get(f"/api/knowledge/documents/{did}").json()
    assert [v["version_no"] for v in d["versions"]] == [1, 2]
    assert d["current_version_id"] == d["versions"][1]["id"]
    assert [n["id"] for n in d["notes"]] == [note["id"]]  # note intact
    hits = c.post("/api/knowledge/search", json={"query": "moderated by firm size micro-enterprises", "mode": "fulltext"}).json()["results"]
    assert hits and hits[0]["document_id"] == did
    old = c.post("/api/knowledge/search", json={"query": "regulatory confidence innovation ecosystems invest", "mode": "fulltext"}).json()["results"]
    assert not any("innovation ecosystems" in h["text"] for h in old)  # stale content no longer indexed
    # original v1 is preserved and downloadable
    f = c.get(f"/api/knowledge/documents/{did}/versions/{d['versions'][0]['id']}/file")
    assert f.status_code == 200 and f.content[:5] == b"%PDF-"
    db = SessionLocal()
    n_emb_v2 = db.scalar(select(func.count(DocumentEmbedding.id)).join(DocumentChunk).where(DocumentChunk.document_id == did))
    db.close()
    assert n_emb_v2 > 0 and n_emb_v1 > 0
    # explicit re-index and embedding regeneration
    j = c.post(f"/api/knowledge/documents/{did}/reindex").json()["job_id"]
    run_jobs()
    assert c.get(f"/api/jobs/{j}").json()["status"] == "succeeded"
    j = c.post(f"/api/knowledge/documents/{did}/reindex?embeddings_only=true").json()["job_id"]
    run_jobs()
    assert c.get(f"/api/jobs/{j}").json()["status"] == "succeeded"
    assert c.get(f"/api/knowledge/documents/{did}").json()["notes"][0]["id"] == note["id"]


def test_metadata_correction_and_duplicates(auth_client):
    c = auth_client
    pdf = make_pdf()
    a = upload(c, data=pdf)
    run_jobs()
    did = a["document"]["id"]
    r = c.patch(f"/api/knowledge/documents/{did}", json={"title": "Corrected title", "year": 2024, "authors": ["A. Researcher"]}).json()
    assert r["title"] == "Corrected title" and r["metadata_source"]["title"] == "researcher"
    b = upload(c, data=pdf, name="same.pdf")
    assert b["document"]["duplicate_of_id"] == did  # identical file flagged
    run_jobs()
    other = upload(c, data=make_pdf(), name="different-bytes.pdf")  # same DOI, different file
    run_jobs()
    dups = c.get(f"/api/knowledge/documents/{other['document']['id']}").json()["extra"]["possible_duplicates"]
    assert any(x["reason"] == "same DOI" for x in dups)
    # re-processing must not overwrite researcher-corrected metadata
    c.post(f"/api/knowledge/documents/{did}/reindex")
    run_jobs()
    assert c.get(f"/api/knowledge/documents/{did}").json()["title"] == "Corrected title"


def test_soft_delete_restore_and_collections(auth_client):
    c = auth_client
    up = upload(c)
    run_jobs()
    did = up["document"]["id"]
    colls = c.get("/api/knowledge/collections").json()
    kinds = {x["kind"] for x in colls}
    assert {"personal_materials", "scholarly_literature", "research_notes", "theory_methods", "research_history"} <= kinds
    lit = next(x for x in colls if x["kind"] == "scholarly_literature")
    assert lit["document_count"] == 1
    custom = c.post("/api/knowledge/collections", json={"name": "Institutional voids"}).json()
    c.post(f"/api/knowledge/collections/{custom['id']}/documents/{did}")
    assert c.get(f"/api/knowledge/documents?collection_id={custom['id']}").json()["total"] == 1
    c.delete(f"/api/knowledge/documents/{did}")
    assert c.get("/api/knowledge/documents").json()["total"] == 0
    assert c.post("/api/knowledge/search", json={"query": "digital trust"}).json()["results"] == []
    c.post(f"/api/knowledge/documents/{did}/restore")
    assert c.get("/api/knowledge/documents").json()["total"] == 1


def test_text_and_docx_ingestion(auth_client):
    from tests.helpers import make_docx

    c = auth_client
    md = b"# Research note on trust\n\nTrust in mobile money platforms depends on agent networks.\n\n## Method\n\nInterviews with agents."
    r = c.post("/api/knowledge/documents/upload", files={"file": ("note.md", md, "text/markdown")}).json()
    d = c.post("/api/knowledge/documents/upload", files={"file": ("sample.docx", make_docx(["Institutional voids matter for entrepreneurs."]),
                                                                  "application/octet-stream")}, data={"is_own_work": "true"}).json()
    run_jobs()
    assert c.get(f"/api/knowledge/documents/{r['document']['id']}").json()["status"] == "ready"
    dd = c.get(f"/api/knowledge/documents/{d['document']['id']}").json()
    assert dd["status"] == "ready" and dd["knowledge_category"] == "researcher_writing" and dd["is_own_work"]
    hits = c.post("/api/knowledge/search", json={"query": "mobile money agent networks"}).json()["results"]
    assert hits[0]["section"].startswith("Research note on trust")


def test_scanned_pdf_reports_clear_failure(auth_client):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    up = upload(auth_client, data=doc.tobytes(), name="scan.pdf")
    run_jobs()
    d = auth_client.get(f"/api/knowledge/documents/{up['document']['id']}").json()
    assert d["status"] == "failed" and "OCR" in d["status_detail"]
    assert auth_client.get(f"/api/jobs/{up['job_id']}").json()["status"] == "failed"


def test_knowledge_stats(auth_client):
    upload(auth_client)
    run_jobs()
    s = auth_client.get("/api/knowledge/stats").json()
    assert s["documents"] == 1 and s["chunks"] >= 3 and s["embeddings"] == s["chunks"]
