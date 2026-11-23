from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, owned, owned_project, parse_uuid
from app.models import (
    CollectionPublication,
    DiscoveryQueueItem,
    Document,
    EvidenceExtraction,
    KnowledgeCollection,
    LiteratureWatch,
    Publication,
    Reference,
    User,
)
from app.services import citations as cit
from app.services.audit import audit
from app.services.jobs import enqueue
from app.services.scholarly import clients
from app.services.scholarly.clients import AuthorRec, PubRecord, ScholarlyError, ScholarlyNotConfigured, ScholarlyRateLimited, normalize_doi
from app.services.scholarly.service import publication_to_dict, upsert_publication, verify_publication
from app.services.search import search_publications

router = APIRouter(prefix="/literature", tags=["literature"])


class SearchIn(BaseModel):
    query: str = Field(default="", max_length=1000)
    sources: list[str] = ["openalex"]
    year_from: int | None = None
    year_to: int | None = None
    pub_type: str | None = None
    open_access: bool | None = None
    author_id: str | None = None
    source_id: str | None = None
    min_citations: int | None = None
    sort: str | None = Field(default=None, pattern=r"^(relevance_score|cited_by_count|publication_date):(asc|desc)$")
    page: int = Field(default=1, ge=1, le=200)
    per_page: int = Field(default=25, ge=1, le=50)
    save: bool = True  # persist metadata records (not into collections)


class WatchIn(BaseModel):
    name: str = Field(min_length=2, max_length=300)
    query: str = ""
    authors: list[str] = []
    filters: dict = {}
    sources: list[str] = ["openalex", "crossref"]
    frequency_days: int = Field(default=7, ge=1, le=365)
    is_active: bool = True
    project_id: str | None = None


class QueueDecisionIn(BaseModel):
    status: str = Field(pattern="^(approved|rejected|saved|pending)$")
    classification: str = ""
    note: str = ""
    collection_id: str | None = None
    project_id: str | None = None  # approved items may be added to a project's references


class ReferenceIn(BaseModel):
    project_id: str
    publication_id: str | None = None
    document_id: str | None = None
    csl: dict | None = None
    citekey: str | None = None
    source_type: str = Field(default="primary", pattern="^(primary|secondary)$")


class PubPatch(BaseModel):
    credibility_note: str | None = None
    title: str | None = None
    abstract: str | None = None


def _save(db, user, recs: list[PubRecord], save: bool) -> list[dict]:
    out = []
    for r in recs:
        if not r.title:
            continue
        if save:
            p = upsert_publication(db, user.id, r)
            d = publication_to_dict(p)
        else:
            d = {**r.to_dict(), "id": None, "authors": [{"name": a.name} for a in r.authors]}
        d["source_db_hit"] = r.source_db
        out.append(d)
    return out


@router.post("/search")
def search(body: SearchIn, user: User = CurrentUser, db: Session = DB) -> dict:
    results, errors, totals = [], {}, {}
    for src in body.sources:
        try:
            if src == "openalex":
                recs, total = clients.openalex_search(body.query, year_from=body.year_from, year_to=body.year_to, pub_type=body.pub_type,
                                                      open_access=body.open_access, author_id=body.author_id, source_id=body.source_id,
                                                      min_citations=body.min_citations, sort=body.sort, page=body.page, per_page=body.per_page)
                totals[src] = total
            elif src == "crossref":
                recs = clients.crossref_search(body.query, rows=body.per_page, year_from=body.year_from, year_to=body.year_to)
            elif src == "semantic_scholar":
                yr = f"{body.year_from or ''}-{body.year_to or ''}" if (body.year_from or body.year_to) else None
                recs = clients.s2_search(body.query, limit=body.per_page, year=yr)
            elif src == "core":
                recs = clients.core_search(body.query, limit=body.per_page)
            elif src == "library":
                results.extend({**publication_to_dict(p), "source_db_hit": "library"} for p in search_publications(db, user.id, body.query, body.per_page))
                continue
            else:
                errors[src] = "unknown source"
                continue
            results.extend(_save(db, user, recs, body.save))
        except ScholarlyNotConfigured as e:
            errors[src] = str(e)
        except ScholarlyRateLimited as e:
            errors[src] = str(e)
        except ScholarlyError as e:
            errors[src] = str(e)
    # merge duplicates across sources by id
    merged, seen = [], set()
    for r in results:
        key = r.get("id") or r.get("doi") or r.get("title")
        if key in seen:
            continue
        seen.add(key)
        merged.append(r)
    db.commit()
    return {"results": merged, "errors": errors, "totals": totals,
            "note": "Records are bibliographic metadata. Abstract availability and full-text availability are shown separately; neither implies credibility."}


@router.get("/doi/{doi:path}")
def lookup_doi(doi: str, user: User = CurrentUser, db: Session = DB) -> dict:
    d = normalize_doi(doi)
    if not d:
        raise HTTPException(422, "Invalid DOI")
    try:
        rec = clients.crossref_work(d)
    except ScholarlyError:
        try:
            rec = clients.openalex_work(d)
        except ScholarlyError as e:
            raise HTTPException(404, f"DOI not found in Crossref or OpenAlex: {e}") from e
    p = upsert_publication(db, user.id, rec)
    p.verification_status, p.verified_at = "verified", datetime.now(timezone.utc)
    db.commit()
    return publication_to_dict(p)


@router.get("/authors")
def authors(q: str, user: User = CurrentUser) -> list[dict]:
    try:
        return clients.openalex_authors(q)
    except ScholarlyError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/institutions")
def institutions(q: str, user: User = CurrentUser) -> list[dict]:
    try:
        return clients.openalex_institutions(q)
    except ScholarlyError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/publications")
def list_publications(q: str | None = None, collection_id: str | None = None, verification_status: str | None = None, limit: int = 50,
                      offset: int = 0, user: User = CurrentUser, db: Session = DB) -> dict:
    if q:
        rows = search_publications(db, user.id, q, limit)
        return {"total": len(rows), "items": [publication_to_dict(p) for p in rows]}
    stmt = select(Publication).where(Publication.owner_id == user.id)
    if collection_id:
        stmt = stmt.where(Publication.id.in_(select(CollectionPublication.publication_id).where(CollectionPublication.collection_id == parse_uuid(collection_id))))
    if verification_status:
        stmt = stmt.where(Publication.verification_status == verification_status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Publication.created_at.desc()).offset(offset).limit(min(limit, 200))).all()
    return {"total": total, "items": [publication_to_dict(p) for p in rows]}


@router.get("/publications/{pub_id}")
def get_publication(pub_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned(db, Publication, pub_id, user)
    d = publication_to_dict(p)
    d["evidence"] = [{"id": str(e.id), "basis": e.basis, "fields": e.fields, "status": e.status, "model": e.model,
                      "knowledge_category": e.knowledge_category, "created_at": e.created_at.isoformat()}
                     for e in db.scalars(select(EvidenceExtraction).where(EvidenceExtraction.publication_id == p.id)).all()]
    d["documents"] = [{"id": str(x.id), "title": x.title, "source_kind": x.source_kind} for x in db.scalars(
        select(Document).where(Document.publication_id == p.id, Document.deleted_at.is_(None))).all()]
    return d


@router.patch("/publications/{pub_id}")
def patch_publication(pub_id: str, body: PubPatch, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned(db, Publication, pub_id, user)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    return publication_to_dict(p)


@router.get("/publications/{pub_id}/related")
def related(pub_id: str, relation: str = "cites", user: User = CurrentUser, db: Session = DB) -> dict:
    """relation: cites (works citing it) | references (works it cites) | related | s2_recommendations"""
    p = owned(db, Publication, pub_id, user)
    try:
        if relation == "s2_recommendations":
            if not p.s2_id and not p.doi:
                raise HTTPException(400, "No Semantic Scholar id or DOI")
            recs = clients.s2_related(p.s2_id or f"DOI:{p.doi}", "recommendations")
        else:
            oid = p.openalex_id
            if not oid and p.doi:
                oid = clients.openalex_work(p.doi).openalex_id
                p.openalex_id = oid
            if not oid:
                raise HTTPException(400, "Publication has no OpenAlex id or DOI")
            recs = clients.openalex_related(oid, relation)
    except ScholarlyError as e:
        raise HTTPException(502, str(e)) from e
    res = _save(db, user, recs, True)
    db.commit()
    return {"relation": relation, "results": res}


@router.post("/publications/{pub_id}/verify")
def verify_pub(pub_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned(db, Publication, pub_id, user)
    res = verify_publication(db, p)
    db.commit()
    return {"publication": publication_to_dict(p), "verification": {"status": res["status"], "comparison": res["comparison"], "checks": res["checks"]}}


@router.post("/publications/{pub_id}/collections/{cid}")
def pub_to_collection(pub_id: str, cid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned(db, Publication, pub_id, user)
    c = owned(db, KnowledgeCollection, cid, user)
    if not db.scalar(select(CollectionPublication).where(CollectionPublication.collection_id == c.id, CollectionPublication.publication_id == p.id)):
        db.add(CollectionPublication(collection_id=c.id, publication_id=p.id))
    db.commit()
    return {"ok": True}


@router.post("/publications/{pub_id}/evidence")
def extract_evidence(pub_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned(db, Publication, pub_id, user)
    doc = db.scalar(select(Document).where(Document.publication_id == p.id, Document.deleted_at.is_(None), Document.source_kind != "doi"))
    params = {"publication_id": str(p.id)}
    if doc:
        params["document_id"] = str(doc.id)
    job = enqueue(db, user.id, "evidence_extraction", params, title=f"Extract evidence: {p.title[:80]}")
    db.commit()
    return {"job_id": str(job.id), "basis": "full_text" if doc else "abstract"}


@router.post("/documents/{doc_id}/evidence")
def extract_doc_evidence(doc_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    d = owned(db, Document, doc_id, user)
    job = enqueue(db, user.id, "evidence_extraction", {"document_id": str(d.id)}, title=f"Extract evidence: {d.title[:80]}")
    db.commit()
    return {"job_id": str(job.id)}


@router.put("/evidence/{eid}")
def update_evidence(eid: str, body: dict, user: User = CurrentUser, db: Session = DB) -> dict:
    e = owned(db, EvidenceExtraction, eid, user)
    if "fields" in body:
        e.fields = body["fields"]
    if body.get("status") in ("ai_extracted", "researcher_verified", "rejected"):
        e.status = body["status"]
        if e.status == "researcher_verified":
            e.knowledge_category = "external_evidence"
    db.commit()
    return {"id": str(e.id), "status": e.status}


# ---------------------------------------------------------------------------
# Watches & review queue
# ---------------------------------------------------------------------------
def _w_out(w: LiteratureWatch) -> dict:
    return {"id": str(w.id), "name": w.name, "query": w.query, "authors": w.authors, "filters": w.filters, "sources": w.sources,
            "frequency_days": w.frequency_days, "is_active": w.is_active, "last_run_at": w.last_run_at.isoformat() if w.last_run_at else None,
            "last_result_count": w.last_result_count, "project_id": str(w.project_id) if w.project_id else None}


@router.get("/watches")
def list_watches(user: User = CurrentUser, db: Session = DB) -> list[dict]:
    return [_w_out(w) for w in db.scalars(select(LiteratureWatch).where(LiteratureWatch.owner_id == user.id).order_by(LiteratureWatch.created_at)).all()]


@router.post("/watches")
def create_watch(body: WatchIn, run_now: bool = True, user: User = CurrentUser, db: Session = DB) -> dict:
    if not body.query and not body.authors:
        raise HTTPException(422, "Provide a query or at least one author")
    w = LiteratureWatch(owner_id=user.id, **body.model_dump(exclude={"project_id"}),
                        project_id=owned_project(db, body.project_id, user).id if body.project_id else None)
    db.add(w)
    db.flush()
    job = enqueue(db, user.id, "literature_watch", {"watch_id": str(w.id)}, title=f"Literature watch: {w.name}") if run_now else None
    db.commit()
    return {**_w_out(w), "job_id": str(job.id) if job else None}


@router.put("/watches/{wid}")
def update_watch(wid: str, body: WatchIn, user: User = CurrentUser, db: Session = DB) -> dict:
    w = owned(db, LiteratureWatch, wid, user)
    for k, v in body.model_dump(exclude={"project_id"}).items():
        setattr(w, k, v)
    db.commit()
    return _w_out(w)


@router.delete("/watches/{wid}")
def delete_watch(wid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    db.delete(owned(db, LiteratureWatch, wid, user))
    db.commit()
    return {"ok": True}


@router.post("/watches/{wid}/run")
def run_watch(wid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    w = owned(db, LiteratureWatch, wid, user)
    job = enqueue(db, user.id, "literature_watch", {"watch_id": str(w.id)}, title=f"Literature watch: {w.name}")
    db.commit()
    return {"job_id": str(job.id)}


@router.get("/queue")
def review_queue(status: str = "pending", limit: int = 100, user: User = CurrentUser, db: Session = DB) -> dict:
    rows = db.execute(select(DiscoveryQueueItem, Publication).join(Publication, Publication.id == DiscoveryQueueItem.publication_id)
                      .where(DiscoveryQueueItem.owner_id == user.id, DiscoveryQueueItem.status == status)
                      .order_by(DiscoveryQueueItem.created_at.desc()).limit(min(limit, 500))).all()
    counts = dict(db.execute(select(DiscoveryQueueItem.status, func.count()).where(DiscoveryQueueItem.owner_id == user.id).group_by(DiscoveryQueueItem.status)).all())
    return {"counts": counts, "items": [{"id": str(q.id), "status": q.status, "classification": q.classification, "note": q.note,
                                         "watch_id": str(q.watch_id) if q.watch_id else None, "created_at": q.created_at.isoformat(),
                                         "publication": publication_to_dict(p)} for q, p in rows]}


@router.post("/queue/{item_id}")
def decide_queue_item(item_id: str, body: QueueDecisionIn, user: User = CurrentUser, db: Session = DB) -> dict:
    q = owned(db, DiscoveryQueueItem, item_id, user)
    q.status, q.classification, q.note, q.decided_at = body.status, body.classification, body.note, datetime.now(timezone.utc)
    p = db.get(Publication, q.publication_id)
    if body.status in ("approved", "saved") and body.collection_id:
        c = owned(db, KnowledgeCollection, body.collection_id, user)
        if not db.scalar(select(CollectionPublication).where(CollectionPublication.collection_id == c.id, CollectionPublication.publication_id == p.id)):
            db.add(CollectionPublication(collection_id=c.id, publication_id=p.id))
    ref = None
    if body.status == "approved" and body.project_id:
        ref = _make_reference(db, user, owned_project(db, body.project_id, user).id, publication=p)
    audit(db, user.id, f"discovery.{body.status}", "publication", p.id, details={"classification": body.classification})
    db.commit()
    return {"ok": True, "reference_id": str(ref.id) if ref else None}


# ---------------------------------------------------------------------------
# Project references (the only sources the Academic Writer may cite)
# ---------------------------------------------------------------------------
def ref_out(r: Reference, style: str = "apa7") -> dict:
    return {"id": str(r.id), "citekey": r.citekey, "csl": r.csl, "verification_status": r.verification_status,
            "verification_detail": r.verification_detail, "source_type": r.source_type,
            "publication_id": str(r.publication_id) if r.publication_id else None, "document_id": str(r.document_id) if r.document_id else None,
            "formatted": cit.format_reference(r.csl or {}, style if style not in ("ieee", "vancouver") else "apa7"),
            "verified_at": r.verified_at.isoformat() if r.verified_at else None}


def _make_reference(db, user, project_id, *, publication: Publication | None = None, document: Document | None = None,
                    csl: dict | None = None, citekey: str | None = None, source_type: str = "primary") -> Reference:
    if publication is not None:
        ex = db.scalar(select(Reference).where(Reference.project_id == project_id, Reference.publication_id == publication.id))
        if ex:
            return ex
        csl = cit.csl_from_publication(publication)
    elif document is not None:
        ex = db.scalar(select(Reference).where(Reference.project_id == project_id, Reference.document_id == document.id))
        if ex:
            return ex
        if document.publication_id:
            publication = db.get(Publication, document.publication_id)
            csl = cit.csl_from_publication(publication)
        else:
            csl = {"title": document.title, "authors": [cit.split_name(a) if isinstance(a, str) else a for a in document.authors or []],
                   "year": document.year, "venue": document.venue, "doi": document.doi, "url": document.url, "type": document.doc_type}
    csl = csl or {}
    existing = {k for (k,) in db.execute(select(Reference.citekey).where(Reference.owner_id == user.id, Reference.project_id == project_id)).all()}
    key = citekey if citekey and citekey not in existing and re.fullmatch(r"[A-Za-z0-9_:\-]+", citekey) else cit.make_citekey(
        csl.get("authors") or [], csl.get("year"), csl.get("title", ""), existing)
    status = "unverified"
    if publication is not None and publication.verification_status in ("verified", "mismatch", "not_found"):
        status = publication.verification_status
    elif document is not None and document.metadata_verified:
        status = "verified"
    r = Reference(owner_id=user.id, project_id=project_id, publication_id=publication.id if publication else None,
                  document_id=document.id if document else None, citekey=key, csl=csl, source_type=source_type, verification_status=status)
    db.add(r)
    db.flush()
    return r


@router.get("/references")
def list_references(project_id: str, style: str = "apa7", user: User = CurrentUser, db: Session = DB) -> list[dict]:
    p = owned_project(db, project_id, user)
    return [ref_out(r, style) for r in db.scalars(select(Reference).where(Reference.project_id == p.id).order_by(Reference.citekey)).all()]


@router.post("/references")
def add_reference(body: ReferenceIn, user: User = CurrentUser, db: Session = DB) -> dict:
    pid = owned_project(db, body.project_id, user).id
    pub = owned(db, Publication, body.publication_id, user) if body.publication_id else None
    doc = owned(db, Document, body.document_id, user) if body.document_id else None
    if not (pub or doc or body.csl):
        raise HTTPException(422, "Provide publication_id, document_id or csl")
    r = _make_reference(db, user, pid, publication=pub, document=doc, csl=body.csl, citekey=body.citekey, source_type=body.source_type)
    audit(db, user.id, "reference.add", "reference", r.id, project_id=pid)
    db.commit()
    return ref_out(r)


@router.put("/references/{rid}")
def edit_reference(rid: str, body: dict, user: User = CurrentUser, db: Session = DB) -> dict:
    r = owned(db, Reference, rid, user)
    if "csl" in body and isinstance(body["csl"], dict):
        r.csl = body["csl"]
        r.verification_status = "unverified"  # edited metadata must be re-verified
    if body.get("source_type") in ("primary", "secondary"):
        r.source_type = body["source_type"]
    db.commit()
    return ref_out(r)


@router.delete("/references/{rid}")
def delete_reference(rid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    r = owned(db, Reference, rid, user)
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.post("/references/verify")
def verify_references(project_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    job = enqueue(db, user.id, "verify_references", {"project_id": str(p.id)}, title="Verify project references", project_id=p.id)
    db.commit()
    return {"job_id": str(job.id)}


def _parse_bibtex(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,]+),(.*?)\n\}", text, re.S):
        fields = dict((k.lower(), re.sub(r"[{}]", "", v).strip()) for k, v in re.findall(r"(\w+)\s*=\s*[{\"](.*?)[}\"]\s*,?\s*$", m.group(3), re.M))
        authors = [cit.split_name(a) for a in re.split(r"\s+and\s+", fields.get("author", "")) if a.strip()]
        out.append({"citekey": m.group(2).strip(), "csl": {"title": fields.get("title", ""), "authors": authors,
                    "year": int(fields["year"][:4]) if fields.get("year", "")[:4].isdigit() else None,
                    "venue": fields.get("journal") or fields.get("booktitle", ""), "volume": fields.get("volume", ""),
                    "issue": fields.get("number", ""), "pages": fields.get("pages", "").replace("--", "-"), "doi": normalize_doi(fields.get("doi")),
                    "url": fields.get("url", ""), "publisher": fields.get("publisher", ""), "type": m.group(1).lower()}})
    return out


def _parse_ris(text: str) -> list[dict]:
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"title": "", "authors": [], "type": val}
        elif cur is None:
            continue
        elif tag in ("AU", "A1"):
            cur["authors"].append(cit.split_name(val))
        elif tag in ("TI", "T1"):
            cur["title"] = val
        elif tag in ("PY", "Y1", "DA") and not cur.get("year"):
            y = re.match(r"(\d{4})", val)
            cur["year"] = int(y.group(1)) if y else None
        elif tag in ("JO", "JF", "T2"):
            cur["venue"] = cur.get("venue") or val
        elif tag == "VL":
            cur["volume"] = val
        elif tag == "IS":
            cur["issue"] = val
        elif tag == "SP":
            cur["pages"] = val
        elif tag == "EP":
            cur["pages"] = f"{cur.get('pages', '')}-{val}"
        elif tag == "DO":
            cur["doi"] = normalize_doi(val)
        elif tag == "UR":
            cur["url"] = val
        elif tag == "PB":
            cur["publisher"] = val
        elif tag == "ER":
            out.append({"csl": cur})
            cur = None
    return out


def _parse_csljson(text: str) -> list[dict]:
    out = []
    for it in json.loads(text):
        issued = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        out.append({"citekey": it.get("id") if isinstance(it.get("id"), str) else None, "csl": {
            "title": it.get("title", ""), "authors": [{"family": a.get("family", a.get("literal", "")), "given": a.get("given", "")} for a in it.get("author") or []],
            "year": issued[0] if issued else None, "venue": it.get("container-title", ""), "volume": str(it.get("volume", "")),
            "issue": str(it.get("issue", "")), "pages": it.get("page", ""), "doi": normalize_doi(it.get("DOI")), "url": it.get("URL", ""),
            "publisher": it.get("publisher", ""), "type": it.get("type", "")}})
    return out


@router.post("/references/import")
async def import_references(project_id: str, file: UploadFile = File(...), user: User = CurrentUser, db: Session = DB) -> dict:
    """Import BibTeX, RIS or CSL-JSON (e.g. exported from Zotero). Imported references start 'unverified'."""
    pid = owned_project(db, project_id, user).id
    raw = (await file.read())[:5_000_000].decode("utf-8", "ignore")
    name = (file.filename or "").lower()
    try:
        entries = _parse_csljson(raw) if name.endswith(".json") else _parse_ris(raw) if name.endswith(".ris") else _parse_bibtex(raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Could not parse references: {e}") from e
    created = []
    for e in entries:
        if not e["csl"].get("title"):
            continue
        r = _make_reference(db, user, pid, csl=e["csl"], citekey=e.get("citekey"))
        created.append(r.citekey)
    db.commit()
    return {"imported": len(created), "citekeys": created, "note": "Run verification to check these against Crossref/OpenAlex."}


@router.post("/references/zotero")
def import_zotero(project_id: str, collection_key: str | None = None, user: User = CurrentUser, db: Session = DB) -> dict:
    """Import from the Zotero Web API (requires ZOTERO_API_KEY and ZOTERO_USER_ID)."""
    import os

    key, uid = os.environ.get("ZOTERO_API_KEY", ""), os.environ.get("ZOTERO_USER_ID", "")
    if not key or not uid:
        raise HTTPException(501, "Zotero integration is not configured (set ZOTERO_API_KEY and ZOTERO_USER_ID). "
                                 "Alternatively export CSL-JSON/BibTeX from Zotero and use /references/import.")
    pid = owned_project(db, project_id, user).id
    path = f"collections/{collection_key}/items" if collection_key else "items"
    try:
        r = httpx.get(f"https://api.zotero.org/users/{uid}/{path}", params={"format": "csljson", "limit": 100, "itemType": "-attachment || note"},
                      headers={"Zotero-API-Key": key, "Zotero-API-Version": "3"}, timeout=60)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Zotero request failed: {e}") from e
    items = r.json().get("items", r.json()) if isinstance(r.json(), dict) else r.json()
    entries = _parse_csljson(json.dumps(items))
    created = [_make_reference(db, user, pid, csl=e["csl"]).citekey for e in entries if e["csl"].get("title")]
    db.commit()
    return {"imported": len(created), "citekeys": created}


_ = AuthorRec
