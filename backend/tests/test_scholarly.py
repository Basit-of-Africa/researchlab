"""Live integration tests against OpenAlex and Crossref (skipped when offline)."""
import pytest

from tests.conftest import network_ok, run_jobs

pytestmark = pytest.mark.skipif(not network_ok(), reason="scholarly APIs unreachable")

KNOWN_DOI = "10.1016/j.jbusvent.2010.08.002"  # Parker (2012), J. Business Venturing — a real Crossref record


def test_openalex_search_with_filters_and_save(auth_client):
    c = auth_client
    r = c.post("/api/literature/search", json={"query": "institutional voids entrepreneurship Africa", "sources": ["openalex"],
                                               "year_from": 2010, "per_page": 10}).json()
    assert not r["errors"], r["errors"]
    assert r["totals"]["openalex"] > 0 and r["results"]
    p = r["results"][0]
    assert p["id"] and p["title"] and p["source_db"] == "openalex"
    assert all((x["year"] or 2010) >= 2010 for x in r["results"])
    for k in ("doi", "abstract_source", "is_oa", "full_text_available", "cited_by_count", "verification_status"):
        assert k in p
    assert p["verification_status"] == "unverified"  # discovery != verification
    lib = c.get("/api/literature/publications").json()
    assert lib["total"] >= len(r["results"])


def test_crossref_doi_verification_and_mismatch(auth_client):
    c = auth_client
    pub = c.get(f"/api/literature/doi/{KNOWN_DOI}").json()
    assert pub["doi"] == KNOWN_DOI and pub["verification_status"] == "verified" and pub["authors"]
    # A project reference with wrong metadata attached to a real DOI must be flagged as a mismatch
    pid = c.post("/api/projects", json={"title": "Verification"}).json()["id"]
    good = c.post("/api/literature/references", json={"project_id": pid, "publication_id": pub["id"]}).json()
    bad = c.post("/api/literature/references", json={"project_id": pid, "csl": {
        "title": "A completely different paper about quantum chromodynamics", "authors": [{"family": "Nobody", "given": "A."}],
        "year": 1999, "doi": KNOWN_DOI}}).json()
    ghost = c.post("/api/literature/references", json={"project_id": pid, "csl": {
        "title": "Fabricated study of nonexistent phenomena xq7", "authors": [{"family": "Invented", "given": "Z."}], "year": 2021,
        "doi": "10.99999/does-not-exist-researchlab-test"}}).json()
    c.post(f"/api/literature/references/verify?project_id={pid}")
    run_jobs()
    refs = {r["id"]: r for r in c.get(f"/api/literature/references?project_id={pid}").json()}
    assert refs[good["id"]]["verification_status"] == "verified"
    assert refs[bad["id"]]["verification_status"] == "mismatch"
    assert refs[ghost["id"]]["verification_status"] == "not_found"


def test_doi_import_and_full_text_availability(auth_client):
    c = auth_client
    r = c.post("/api/knowledge/documents/doi", json={"doi": KNOWN_DOI}).json()["document"]
    assert r["metadata_verified"] is True and r["source_kind"] == "doi"
    assert "no full text stored" in r["status_detail"].lower()
    assert c.post("/api/knowledge/documents/doi", json={"doi": "10.99999/nope-researchlab"}).status_code == 404


def test_citation_graph_and_collections(auth_client):
    c = auth_client
    pub = c.get(f"/api/literature/doi/{KNOWN_DOI}").json()
    rel = c.get(f"/api/literature/publications/{pub['id']}/related?relation=cites").json()
    assert rel["results"], "expected citing works"
    coll = next(x for x in c.get("/api/knowledge/collections").json() if x["kind"] == "scholarly_literature")
    assert c.post(f"/api/literature/publications/{rel['results'][0]['id']}/collections/{coll['id']}").status_code == 200
    assert c.get(f"/api/literature/publications?collection_id={coll['id']}").json()["total"] == 1


def test_literature_watch_feeds_review_queue(auth_client):
    c = auth_client
    pid = c.post("/api/projects", json={"title": "Watch"}).json()["id"]
    w = c.post("/api/literature/watches", json={"name": "Digital trust", "query": "digital trust infrastructure", "sources": ["openalex"],
                                                "filters": {"year_from": 2023}}).json()
    run_jobs()
    q = c.get("/api/literature/queue").json()
    assert q["counts"].get("pending", 0) > 0
    item = q["items"][0]
    assert item["watch_id"] == w["id"]
    r = c.post(f"/api/literature/queue/{item['id']}", json={"status": "approved", "classification": "core", "project_id": pid}).json()
    assert r["reference_id"]
    assert c.get("/api/literature/queue?status=approved").json()["items"][0]["classification"] == "core"
    rej = q["items"][1]
    c.post(f"/api/literature/queue/{rej['id']}", json={"status": "rejected"})
    assert c.get("/api/literature/queue?status=rejected").json()["items"]
