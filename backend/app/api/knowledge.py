from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, owned, owned_project, parse_uuid
from app.config import get_settings
from app.models import (
    CollectionDocument,
    CollectionPublication,
    Document,
    DocumentChunk,
    DocumentEmbedding,
    DocumentVersion,
    KnowledgeCategory,
    KnowledgeCollection,
    Publication,
    ResearchDecision,
    ResearchNote,
    User,
    Visibility,
)
from app.services import embeddings, knowledge
from app.services.agents.core import ContextItem, kb_items, run_agent
from app.services.agents.workflows import note_items
from app.services.audit import audit
from app.services.jobs import enqueue
from app.services.scholarly.clients import ScholarlyError
from app.services.search import SearchFilters, hybrid_search, search_notes
from app.services.storage import get_storage

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

MIME_BY_EXT = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
               ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown"}


def validate_upload(filename: str, data: bytes) -> str:
    s = get_settings()
    name = (filename or "").lower()
    ext = next((e for e in MIME_BY_EXT if name.endswith(e)), None)
    if not ext:
        raise HTTPException(415, "Unsupported file type. Allowed: PDF, DOCX, TXT, Markdown")
    if len(data) == 0:
        raise HTTPException(400, "Empty file")
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {s.max_upload_mb} MB limit")
    if ext == ".pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(415, "File content is not a PDF")
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" not in z.namelist():
                    raise HTTPException(415, "File is not a valid DOCX document")
        except zipfile.BadZipFile:
            raise HTTPException(415, "File is not a valid DOCX document") from None
    if ext in (".txt", ".md", ".markdown"):
        try:
            data[:200000].decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(415, "Text files must be UTF-8 encoded") from None
    return MIME_BY_EXT[ext]


class DocPatch(BaseModel):
    title: str | None = None
    authors: list[str] | None = None
    year: int | None = Field(default=None, ge=1000, le=2100)
    abstract: str | None = None
    doi: str | None = None
    url: str | None = None
    venue: str | None = None
    doc_type: str | None = None
    visibility: str | None = None
    knowledge_category: str | None = None
    is_own_work: bool | None = None
    project_id: str | None = None


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = ""


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: str = Field(default="hybrid", pattern="^(hybrid|fulltext|semantic)$")
    k: int = Field(default=10, ge=1, le=50)
    collection_ids: list[str] = []
    project_id: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    doc_types: list[str] = []
    knowledge_categories: list[str] = []
    own_work_only: bool = False
    include_notes: bool = True


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    project_id: str | None = None
    collection_ids: list[str] = []
    mode: str = Field(default="knowledge", pattern="^(knowledge|development)$")


class DOIIn(BaseModel):
    doi: str
    project_id: str | None = None
    collection_ids: list[str] = []


class URLIn(BaseModel):
    url: str = Field(max_length=2000)
    project_id: str | None = None
    visibility: str = Visibility.public_source.value
    collection_ids: list[str] = []


def _colls(db, user, ids: list[str]) -> list:
    return [owned(db, KnowledgeCollection, i, user).id for i in ids]


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------
@router.get("/collections")
def list_collections(user: User = CurrentUser, db: Session = DB) -> list[dict]:
    knowledge.ensure_system_collections(db, user.id)
    db.commit()
    rows = db.execute(
        select(KnowledgeCollection, func.count(CollectionDocument.id))
        .outerjoin(CollectionDocument, CollectionDocument.collection_id == KnowledgeCollection.id)
        .where(KnowledgeCollection.owner_id == user.id).group_by(KnowledgeCollection.id).order_by(KnowledgeCollection.is_system.desc(), KnowledgeCollection.name)
    ).all()
    pubs = dict(db.execute(select(CollectionPublication.collection_id, func.count()).join(KnowledgeCollection)
                           .where(KnowledgeCollection.owner_id == user.id).group_by(CollectionPublication.collection_id)).all())
    return [{"id": str(c.id), "name": c.name, "kind": c.kind, "description": c.description, "is_system": c.is_system,
             "document_count": n, "publication_count": pubs.get(c.id, 0)} for c, n in rows]


@router.post("/collections")
def create_collection(body: CollectionIn, user: User = CurrentUser, db: Session = DB) -> dict:
    if db.scalar(select(KnowledgeCollection).where(KnowledgeCollection.owner_id == user.id, KnowledgeCollection.name == body.name)):
        raise HTTPException(409, "A collection with that name exists")
    c = KnowledgeCollection(owner_id=user.id, name=body.name, description=body.description)
    db.add(c)
    db.commit()
    return {"id": str(c.id), "name": c.name}


@router.put("/collections/{cid}")
def rename_collection(cid: str, body: CollectionIn, user: User = CurrentUser, db: Session = DB) -> dict:
    c = owned(db, KnowledgeCollection, cid, user)
    if c.is_system:
        raise HTTPException(400, "System collections cannot be renamed")
    c.name, c.description = body.name, body.description
    db.commit()
    return {"id": str(c.id), "name": c.name}


@router.delete("/collections/{cid}")
def delete_collection(cid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    c = owned(db, KnowledgeCollection, cid, user)
    if c.is_system:
        raise HTTPException(400, "System collections cannot be deleted")
    db.delete(c)  # documents themselves are kept
    db.commit()
    return {"ok": True}


@router.post("/collections/{cid}/documents/{doc_id}")
def add_doc_to_collection(cid: str, doc_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    c = owned(db, KnowledgeCollection, cid, user)
    d = owned(db, Document, doc_id, user)
    knowledge.add_to_collection(db, c.id, d.id)
    db.commit()
    return {"ok": True}


@router.delete("/collections/{cid}/documents/{doc_id}")
def remove_doc_from_collection(cid: str, doc_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    c = owned(db, KnowledgeCollection, cid, user)
    row = db.scalar(select(CollectionDocument).where(CollectionDocument.collection_id == c.id, CollectionDocument.document_id == parse_uuid(doc_id)))
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
@router.get("/documents")
def list_documents(collection_id: str | None = None, project_id: str | None = None, status: str | None = None, q: str | None = None,
                   knowledge_category: str | None = None, include_deleted: bool = False, limit: int = 100, offset: int = 0,
                   user: User = CurrentUser, db: Session = DB) -> dict:
    stmt = select(Document).where(Document.owner_id == user.id)
    if not include_deleted:
        stmt = stmt.where(Document.deleted_at.is_(None))
    if collection_id:
        stmt = stmt.where(Document.id.in_(select(CollectionDocument.document_id).where(CollectionDocument.collection_id == parse_uuid(collection_id))))
    if project_id:
        stmt = stmt.where(Document.project_id == parse_uuid(project_id))
    if status:
        stmt = stmt.where(Document.status == status)
    if knowledge_category:
        stmt = stmt.where(Document.knowledge_category == knowledge_category)
    if q:
        stmt = stmt.where(Document.title.ilike(f"%{q[:200]}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Document.created_at.desc()).offset(offset).limit(min(limit, 500))).all()
    return {"total": total, "items": [knowledge.document_to_dict(d) for d in rows]}


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(""),
    doc_type: str = Form("article"),
    visibility: str = Form(Visibility.private_unpublished.value),
    knowledge_category: str = Form(KnowledgeCategory.external_evidence.value),
    is_own_work: bool = Form(False),
    project_id: str = Form(""),
    collection_ids: str = Form(""),
    verify_metadata: bool = Form(True),
    user: User = CurrentUser,
    db: Session = DB,
) -> dict:
    data = await file.read()
    mime = validate_upload(file.filename or "", data)
    if visibility not in {v.value for v in Visibility}:
        raise HTTPException(422, "invalid visibility")
    if knowledge_category not in {c.value for c in KnowledgeCategory}:
        raise HTTPException(422, "invalid knowledge_category")
    pid = owned_project(db, project_id, user).id if project_id else None
    cids = _colls(db, user, [c for c in collection_ids.split(",") if c.strip()])
    if is_own_work and knowledge_category == KnowledgeCategory.external_evidence.value:
        knowledge_category = KnowledgeCategory.researcher_writing.value
    doc = knowledge.create_document(db, user.id, data=data, filename=file.filename or "upload", mime_type=mime, title=title,
                                    doc_type=doc_type, visibility=visibility, knowledge_category=knowledge_category,
                                    is_own_work=is_own_work, project_id=pid, collection_ids=cids,
                                    metadata={"_source": "researcher"} if title else None)
    if title:
        doc.metadata_source = {"title": "researcher"}
    v = doc.versions[-1] if doc.versions else db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == doc.id))
    job = enqueue(db, user.id, "ingest_document", {"version_id": str(v.id), "verify_metadata": verify_metadata}, title=f"Process: {file.filename}",
                  project_id=pid)
    audit(db, user.id, "document.upload", "document", doc.id, project_id=pid, details={"filename": file.filename, "size": len(data)})
    db.commit()
    return {"document": knowledge.document_to_dict(doc), "job_id": str(job.id)}


@router.post("/documents/doi")
def import_doi(body: DOIIn, user: User = CurrentUser, db: Session = DB) -> dict:
    pid = owned_project(db, body.project_id, user).id if body.project_id else None
    try:
        doc = knowledge.import_doi(db, user.id, body.doi, project_id=pid, collection_ids=_colls(db, user, body.collection_ids))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except ScholarlyError as e:
        raise HTTPException(404 if "not found" in str(e) else 502, f"DOI could not be resolved: {e}") from e
    audit(db, user.id, "document.import_doi", "document", doc.id, project_id=pid, details={"doi": body.doi})
    db.commit()
    return {"document": knowledge.document_to_dict(doc)}


@router.post("/documents/url")
def import_url(body: URLIn, user: User = CurrentUser, db: Session = DB) -> dict:
    pid = owned_project(db, body.project_id, user).id if body.project_id else None
    try:
        data, name, ctype = knowledge.fetch_url(body.url, get_settings().max_upload_mb * 1024 * 1024)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not fetch URL: {e}") from e
    mime = validate_upload(name, data)
    doc = knowledge.create_document(db, user.id, data=data, filename=name, mime_type=mime, source_kind="url", visibility=body.visibility,
                                    project_id=pid, collection_ids=_colls(db, user, body.collection_ids), metadata={"url": body.url, "_source": "researcher"})
    v = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == doc.id))
    job = enqueue(db, user.id, "ingest_document", {"version_id": str(v.id)}, title=f"Process URL: {body.url[:80]}", project_id=pid)
    audit(db, user.id, "document.import_url", "document", doc.id, project_id=pid, details={"url": body.url})
    db.commit()
    return {"document": knowledge.document_to_dict(doc), "job_id": str(job.id)}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    d = owned(db, Document, doc_id, user)
    out = knowledge.document_to_dict(d, with_versions=True)
    out["collections"] = [{"id": str(c.id), "name": c.name} for c in db.scalars(
        select(KnowledgeCollection).join(CollectionDocument).where(CollectionDocument.document_id == d.id)).all()]
    out["notes"] = [{"id": str(n.id), "title": n.title, "body": n.body, "knowledge_category": n.knowledge_category, "anchor": n.anchor}
                    for n in db.scalars(select(ResearchNote).where(ResearchNote.document_id == d.id)).all()]
    out["chunk_count"] = db.scalar(select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == d.id))
    out["embedding_models"] = [m for (m,) in db.execute(select(DocumentEmbedding.model).join(DocumentChunk).where(
        DocumentChunk.document_id == d.id).distinct()).all()]
    if d.current_version_id:
        v = db.get(DocumentVersion, d.current_version_id)
        out["extracted_references"] = (v.extracted_references or [])[:200]
    return out


@router.get("/documents/{doc_id}/chunks")
def document_chunks(doc_id: str, limit: int = 200, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    d = owned(db, Document, doc_id, user)
    return [{"id": str(c.id), "index": c.chunk_index, "section": c.section, "page_start": c.page_start, "page_end": c.page_end,
             "text": c.text} for c in db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == d.id).order_by(DocumentChunk.chunk_index).limit(limit)).all()]


@router.patch("/documents/{doc_id}")
def correct_metadata(doc_id: str, body: DocPatch, user: User = CurrentUser, db: Session = DB) -> dict:
    """Metadata correction. Does not trigger re-chunking (only affected records change)."""
    d = owned(db, Document, doc_id, user)
    ch = body.model_dump(exclude_unset=True)
    before = {k: getattr(d, k) for k in ch if k != "project_id"}
    src = dict(d.metadata_source or {})
    for k, v in ch.items():
        if k == "project_id":
            d.project_id = owned_project(db, v, user).id if v else None
            continue
        if k == "visibility" and v not in {x.value for x in Visibility}:
            raise HTTPException(422, "invalid visibility")
        if k == "knowledge_category" and v not in {x.value for x in KnowledgeCategory}:
            raise HTTPException(422, "invalid knowledge_category")
        setattr(d, k, v)
        src[k] = "researcher"
    d.metadata_source = src
    audit(db, user.id, "document.metadata_corrected", "document", d.id, project_id=d.project_id,
          details={"before": json.loads(json.dumps(before, default=str)), "after": json.loads(json.dumps(ch, default=str))})
    db.commit()
    return knowledge.document_to_dict(d)


@router.post("/documents/{doc_id}/replace")
async def replace_document(doc_id: str, file: UploadFile = File(...), change_note: str = Form(""), user: User = CurrentUser, db: Session = DB) -> dict:
    """Upload a new version. The original file and all notes/references are preserved."""
    d = owned(db, Document, doc_id, user)
    data = await file.read()
    mime = validate_upload(file.filename or "", data)
    v = knowledge.add_version(db, d, data, file.filename or "upload", mime, change_note=change_note or "Replacement upload")
    job = enqueue(db, user.id, "ingest_document", {"version_id": str(v.id)}, title=f"Process new version: {file.filename}", project_id=d.project_id)
    audit(db, user.id, "document.replace", "document", d.id, project_id=d.project_id, details={"version_no": v.version_no})
    db.commit()
    db.refresh(d)
    return {"document": knowledge.document_to_dict(d, with_versions=True), "job_id": str(job.id)}


@router.post("/documents/{doc_id}/reindex")
def reindex(doc_id: str, embeddings_only: bool = False, user: User = CurrentUser, db: Session = DB) -> dict:
    d = owned(db, Document, doc_id, user)
    job = enqueue(db, user.id, "reindex_document", {"document_id": str(d.id), "embeddings_only": embeddings_only},
                  title=("Regenerate embeddings: " if embeddings_only else "Re-index: ") + d.title[:80])
    db.commit()
    return {"job_id": str(job.id)}


@router.post("/documents/{doc_id}/verify-metadata")
def verify_doc_metadata(doc_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    d = owned(db, Document, doc_id, user)
    if not d.doi:
        raise HTTPException(400, "Add a DOI first; title-based lookup would disclose the title of a possibly unpublished document")
    rec = knowledge.enrich_from_registry(db, d)
    db.commit()
    return {"document": knowledge.document_to_dict(d), "registry_record": rec}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, permanent: bool = False, user: User = CurrentUser, db: Session = DB) -> dict:
    d = owned(db, Document, doc_id, user)
    if not permanent:
        d.deleted_at = datetime.now(timezone.utc)
        audit(db, user.id, "document.soft_delete", "document", d.id, project_id=d.project_id)
        db.commit()
        return {"ok": True, "restorable": True}
    keys = [v.storage_key for v in d.versions if v.storage_key]
    audit(db, user.id, "document.permanent_delete", "document", d.id, details={"title": d.title, "versions": len(keys)})
    db.delete(d)  # notes keep their content (document_id set NULL)
    db.commit()
    st = get_storage()
    for k in keys:
        try:
            st.delete(k)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "restorable": False}


@router.post("/documents/{doc_id}/restore")
def restore_document(doc_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    d = owned(db, Document, doc_id, user)
    d.deleted_at = None
    audit(db, user.id, "document.restore", "document", d.id)
    db.commit()
    return knowledge.document_to_dict(d)


@router.get("/documents/{doc_id}/versions/{version_id}/file")
def download_original(doc_id: str, version_id: str, user: User = CurrentUser, db: Session = DB):
    d = owned(db, Document, doc_id, user)
    v = db.get(DocumentVersion, parse_uuid(version_id))
    if not v or v.document_id != d.id or not v.storage_key:
        raise HTTPException(404, "File not found")
    data = get_storage().get(v.storage_key)
    return Response(data, media_type=v.mime_type or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{v.filename}"'})


# ---------------------------------------------------------------------------
# Retrieval & intelligence
# ---------------------------------------------------------------------------
def _filters(db, user, body) -> SearchFilters:
    return SearchFilters(collection_ids=[parse_uuid(c) for c in body.collection_ids], project_id=parse_uuid(body.project_id) if getattr(body, "project_id", None) and getattr(body, "restrict_to_project", False) else None,
                         year_from=getattr(body, "year_from", None), year_to=getattr(body, "year_to", None), doc_types=getattr(body, "doc_types", []),
                         knowledge_categories=getattr(body, "knowledge_categories", []), own_work_only=getattr(body, "own_work_only", False))


@router.post("/search")
def search(body: SearchIn, user: User = CurrentUser, db: Session = DB) -> dict:
    try:
        hits = hybrid_search(db, user.id, body.query, _filters(db, user, body), k=body.k, mode=body.mode)
    except embeddings.EmbeddingError as e:
        raise HTTPException(503, f"Semantic search unavailable: {e}") from e
    notes = search_notes(db, user.id, body.query, k=8) if body.include_notes else []
    return {"results": hits, "notes": notes, "embedding_model": embeddings.model_name()}


@router.post("/ask")
def ask(body: AskIn, user: User = CurrentUser, db: Session = DB) -> dict:
    """Answer a question from the knowledge base with provenance (sync)."""
    pid = owned_project(db, body.project_id, user).id if body.project_id else None
    f = SearchFilters(collection_ids=[parse_uuid(c) for c in body.collection_ids])
    hits = hybrid_search(db, user.id, body.question, f, k=12)
    own = hybrid_search(db, user.id, body.question, SearchFilters(own_work_only=True), k=6)
    seen = {h["chunk_id"] for h in hits}
    hits += [h for h in own if h["chunk_id"] not in seen]
    notes = search_notes(db, user.id, body.question, k=8)
    items: list[ContextItem] = note_items(notes) + kb_items(hits)
    if body.mode == "development":
        decs = db.scalars(select(ResearchDecision).where(ResearchDecision.owner_id == user.id).order_by(ResearchDecision.created_at).limit(60)).all()
        if decs:
            items.insert(0, ContextItem("foundation", "RESEARCHER decision history (chronological)", "\n".join(
                f"- {d.created_at.date()} {d.decision.upper()} {d.decision_type}: {d.subject[:200]}{' — ' + d.rationale[:150] if d.rationale else ''}" for d in decs), private=True))
        dated = sorted([n for n in notes], key=lambda n: n["created_at"])
        if dated:
            items.insert(0, ContextItem("foundation", "Chronology of the researcher's notes on this question", "\n".join(
                f"- {n['created_at'][:10]} [{n['knowledge_category']}] {n['title'] or n['body'][:120]}" for n in dated), private=True))
        instruction = (f"Knowledge-development question: {body.question}\n\nTrace how the researcher's understanding has evolved, using dates "
                       "where available. Distinguish clearly: (a) the researcher's established positions, (b) provisional opinions, (c) revised "
                       "positions, (d) external evidence that supports or challenges them, (e) your own interpretation (label it). Cite [S#]. "
                       "If the record is too thin to answer, say so.")
    else:
        instruction = (f"Question about the researcher's knowledge base: {body.question}\n\nAnswer from the CONTEXT only, citing [S#] for every "
                       "claim. Group the answer by source category (researcher's own work/notes vs external literature). Note contradictions. "
                       "Finish with 'Gaps in the knowledge base' if relevant.")
    out = run_agent(db, user.id, "literature", f"knowledge_{body.mode}", instruction, context=items, project_id=pid, max_tokens=3000)
    db.commit()
    return {"answer": out.text, "sources": out.sources, "excluded_private_sources": out.excluded_private, "knowledge_category": "ai_interpretation",
            "provider": out.provider, "model": out.model}


@router.get("/stats")
def stats(user: User = CurrentUser, db: Session = DB) -> dict:
    base = select(func.count(Document.id)).where(Document.owner_id == user.id, Document.deleted_at.is_(None))
    by_status = dict(db.execute(select(Document.status, func.count()).where(Document.owner_id == user.id, Document.deleted_at.is_(None)).group_by(Document.status)).all())
    by_cat = dict(db.execute(select(Document.knowledge_category, func.count()).where(Document.owner_id == user.id, Document.deleted_at.is_(None)).group_by(Document.knowledge_category)).all())
    chunks = db.scalar(select(func.count(DocumentChunk.id)).join(Document).where(Document.owner_id == user.id, Document.deleted_at.is_(None)))
    emb = db.scalar(select(func.count(DocumentEmbedding.id)).join(DocumentChunk).join(Document).where(Document.owner_id == user.id))
    authors = db.execute(select(func.jsonb_array_elements_text(Document.authors).label("a"), func.count()).where(
        Document.owner_id == user.id, Document.deleted_at.is_(None)).group_by("a").order_by(func.count().desc()).limit(15)).all()
    return {"documents": db.scalar(base), "by_status": by_status, "by_knowledge_category": by_cat, "chunks": chunks, "embeddings": emb,
            "publications": db.scalar(select(func.count(Publication.id)).where(Publication.owner_id == user.id)),
            "notes": db.scalar(select(func.count(ResearchNote.id)).where(ResearchNote.owner_id == user.id)),
            "frequent_authors": [{"author": a, "documents": n} for a, n in authors], "embedding_model": embeddings.model_name()}
