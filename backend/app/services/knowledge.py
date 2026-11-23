"""Continuous knowledge-base management: ingestion, versioning, re-indexing."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    CollectionDocument,
    CollectionKind,
    Document,
    DocumentChunk,
    DocumentEmbedding,
    DocumentVersion,
    KnowledgeCategory,
    KnowledgeCollection,
    Visibility,
)
from app.services import embeddings
from app.services.chunking import chunk_blocks
from app.services.extraction import YEAR_RE, extract
from app.services.scholarly import clients
from app.services.scholarly.clients import ScholarlyError, normalize_doi
from app.services.scholarly.service import title_similarity
from app.services.storage import get_storage, make_key, sha256_bytes

log = logging.getLogger("researchlab.knowledge")

SYSTEM_COLLECTIONS = [
    ("Personal academic materials", CollectionKind.personal_materials, "Published papers, manuscripts, theses, proposals and writing samples."),
    ("Scholarly literature", CollectionKind.scholarly_literature, "Journal articles, books, reports and other external literature."),
    ("Research notes", CollectionKind.research_notes, "Observations, ideas, annotations and researcher-generated arguments."),
    ("Theory and methods", CollectionKind.theory_methods, "Theories, frameworks, models, methods and instruments."),
    ("Research history", CollectionKind.research_history, "Previous projects, decisions, feedback and lessons."),
]

ALLOWED_EXT = (".pdf", ".docx", ".txt", ".md", ".markdown")


def ensure_system_collections(db: Session, owner_id: uuid.UUID) -> dict[str, KnowledgeCollection]:
    existing = {c.kind: c for c in db.scalars(select(KnowledgeCollection).where(
        KnowledgeCollection.owner_id == owner_id, KnowledgeCollection.is_system.is_(True))).all()}
    for name, kind, desc in SYSTEM_COLLECTIONS:
        if kind.value not in existing:
            c = KnowledgeCollection(owner_id=owner_id, name=name, kind=kind.value, description=desc, is_system=True)
            db.add(c)
            existing[kind.value] = c
    db.flush()
    return existing


def add_to_collection(db: Session, collection_id: uuid.UUID, document_id: uuid.UUID) -> None:
    exists = db.scalar(select(CollectionDocument).where(
        CollectionDocument.collection_id == collection_id, CollectionDocument.document_id == document_id))
    if not exists:
        db.add(CollectionDocument(collection_id=collection_id, document_id=document_id))


def default_collection_kind(doc: Document) -> str:
    if doc.is_own_work or doc.knowledge_category == KnowledgeCategory.researcher_writing.value:
        return CollectionKind.personal_materials.value
    if doc.doc_type in ("theory", "method", "instrument"):
        return CollectionKind.theory_methods.value
    return CollectionKind.scholarly_literature.value


def find_duplicates(db: Session, owner_id: uuid.UUID, *, content_hash: str = "", doi: str | None = None,
                    title: str = "", exclude_id: uuid.UUID | None = None) -> list[dict]:
    out = []
    q = select(Document).where(Document.owner_id == owner_id, Document.deleted_at.is_(None))
    if exclude_id:
        q = q.where(Document.id != exclude_id)
    if content_hash:
        for v in db.scalars(select(DocumentVersion).join(Document).where(
                Document.owner_id == owner_id, Document.deleted_at.is_(None), DocumentVersion.content_hash == content_hash)).all():
            if v.document_id != exclude_id:
                out.append({"document_id": str(v.document_id), "reason": "identical file"})
    if doi:
        for d in db.scalars(q.where(Document.doi == doi)).all():
            out.append({"document_id": str(d.id), "reason": "same DOI"})
    if title and len(title) > 15:
        for d in db.scalars(q.where(func.lower(func.left(Document.title, 12)) == title[:12].lower())).all():
            if title_similarity(d.title, title) > 0.92:
                out.append({"document_id": str(d.id), "reason": "near-identical title"})
    seen, uniq = set(), []
    for o in out:
        if o["document_id"] not in seen:
            seen.add(o["document_id"])
            uniq.append(o)
    return uniq


def create_document(
    db: Session,
    owner_id: uuid.UUID,
    *,
    data: bytes | None,
    filename: str = "",
    mime_type: str = "",
    title: str = "",
    doc_type: str = "article",
    source_kind: str = "upload",
    visibility: str = Visibility.private_unpublished.value,
    knowledge_category: str = KnowledgeCategory.external_evidence.value,
    is_own_work: bool = False,
    project_id: uuid.UUID | None = None,
    collection_ids: list[uuid.UUID] | None = None,
    metadata: dict | None = None,
) -> Document:
    doc = Document(
        owner_id=owner_id, title=title, doc_type=doc_type, source_kind=source_kind, visibility=visibility,
        knowledge_category=knowledge_category, is_own_work=is_own_work, project_id=project_id, status="pending",
    )
    meta = metadata or {}
    for f in ("authors", "year", "abstract", "doi", "url", "venue"):
        if meta.get(f):
            setattr(doc, f, meta[f])
    if meta:
        doc.metadata_source = {k: meta.get("_source", "researcher") for k in meta if not k.startswith("_")}
    db.add(doc)
    db.flush()
    colls = ensure_system_collections(db, owner_id)
    add_to_collection(db, colls[default_collection_kind(doc)].id, doc.id)
    for cid in collection_ids or []:
        add_to_collection(db, cid, doc.id)
    if data is not None:
        add_version(db, doc, data, filename, mime_type, change_note="Initial upload")
    else:
        doc.status = "ready" if not data else doc.status
    return doc


def add_version(db: Session, doc: Document, data: bytes, filename: str, mime_type: str, change_note: str = "") -> DocumentVersion:
    h = sha256_bytes(data)
    key = make_key(doc.owner_id, "documents", filename, h)
    get_storage().put(key, data, mime_type or "application/octet-stream")
    n = (db.scalar(select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == doc.id)) or 0) + 1
    v = DocumentVersion(document_id=doc.id, version_no=n, storage_key=key, filename=filename, mime_type=mime_type,
                        size_bytes=len(data), content_hash=h, change_note=change_note, processing_status="pending")
    db.add(v)
    db.flush()
    doc.status = "pending"
    dups = find_duplicates(db, doc.owner_id, content_hash=h, exclude_id=doc.id)
    if dups:
        doc.extra = {**(doc.extra or {}), "possible_duplicates": dups}
        doc.duplicate_of_id = uuid.UUID(dups[0]["document_id"])
    return v


def _apply_extracted_metadata(doc: Document, meta: dict) -> None:
    src = dict(doc.metadata_source or {})

    def setf(field: str, value) -> None:
        if value and not getattr(doc, field) and src.get(field) != "researcher":
            setattr(doc, field, value)
            src[field] = "extracted"

    setf("title", meta.get("title"))
    setf("abstract", meta.get("abstract"))
    d = normalize_doi(meta.get("doi"))
    setf("doi", d)
    if not doc.year and meta.get("year_candidates"):
        yrs = [y for y in meta["year_candidates"] if y <= datetime.now().year]
        if yrs:
            doc.year, src["year"] = max(yrs), "extracted (heuristic)"
    if not doc.authors:
        a = meta.get("pdf_author") or meta.get("docx_author")
        if a:
            doc.authors = [x.strip() for x in re.split(r";|,| and ", a) if x.strip()][:30]
            src["authors"] = "extracted (file metadata)"
    if meta.get("keywords"):
        doc.extra = {**(doc.extra or {}), "keywords": meta["keywords"]}
    doc.metadata_source = src


def enrich_from_registry(db: Session, doc: Document) -> dict | None:
    """Verify/complete metadata from Crossref using the DOI only (no content is sent)."""
    if not doc.doi:
        return None
    try:
        rec = clients.crossref_work(doc.doi)
    except ScholarlyError as e:
        doc.extra = {**(doc.extra or {}), "registry_check": {"status": "failed", "error": str(e)}}
        return None
    src = dict(doc.metadata_source or {})
    mismatch = bool(doc.title) and title_similarity(doc.title, rec.title) < 0.6 and src.get("title") == "researcher"
    for field, val in (("title", rec.title), ("year", rec.year), ("venue", rec.venue), ("abstract", rec.abstract)):
        if val and src.get(field) != "researcher":
            setattr(doc, field, val)
            src[field] = "crossref"
    if rec.authors and src.get("authors") != "researcher":
        doc.authors = [a.name for a in rec.authors]
        src["authors"] = "crossref"
    doc.metadata_source = src
    doc.metadata_verified = not mismatch
    doc.extra = {**(doc.extra or {}), "registry_check": {"status": "mismatch" if mismatch else "verified", "source": "crossref",
                                                          "checked_at": datetime.now(timezone.utc).isoformat()}}
    return rec.to_dict()


def process_version(db: Session, version: DocumentVersion, *, verify_metadata: bool = True) -> dict:
    """Extract → metadata → chunk → embed → index. Reuses embeddings of unchanged chunks."""
    doc = db.get(Document, version.document_id)
    assert doc is not None
    doc.status, version.processing_status = "processing", "processing"
    db.commit()
    data = get_storage().get(version.storage_key)
    fmt, ex = extract(version.filename, data)
    version.extracted_text = ex.text
    version.page_count = ex.page_count
    version.headings = ex.headings
    version.extracted_metadata = {k: v for k, v in ex.metadata.items() if k != "pdf_metadata"}
    version.extracted_references = ex.references
    _apply_extracted_metadata(doc, ex.metadata)
    if verify_metadata and doc.doi:
        enrich_from_registry(db, doc)
    dups = find_duplicates(db, doc.owner_id, doi=doc.doi, title=doc.title, exclude_id=doc.id)
    if dups:
        doc.extra = {**(doc.extra or {}), "possible_duplicates": dups}
    stats = reindex_version(db, doc, version, ex.blocks)
    version.processing_status = "ready"
    doc.current_version_id = version.id
    doc.status, doc.status_detail = "ready", ""
    db.commit()
    return {"format": fmt, "pages": ex.page_count, "headings": len(ex.headings), "references": len(ex.references), **stats}


def reindex_version(db: Session, doc: Document, version: DocumentVersion, blocks=None) -> dict:
    if blocks is None:
        _, ex = extract(version.filename, get_storage().get(version.storage_key))
        blocks = ex.blocks
    chunks = chunk_blocks(blocks)
    model = embeddings.model_name()
    # Embeddings from any previous version of this document, keyed by chunk content hash
    reusable = {
        h: e for h, e in db.execute(
            select(DocumentEmbedding.content_hash, DocumentEmbedding.embedding)
            .join(DocumentChunk, DocumentChunk.id == DocumentEmbedding.chunk_id)
            .where(DocumentChunk.document_id == doc.id, DocumentEmbedding.model == model)
        ).all()
    }
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    new_rows, to_embed = [], []
    for c in chunks:
        row = DocumentChunk(document_id=doc.id, version_id=version.id, chunk_index=c.index, text=c.text, section=c.section,
                            page_start=c.page_start, page_end=c.page_end, char_start=c.char_start, char_end=c.char_end,
                            token_estimate=c.token_estimate, content_hash=c.content_hash)
        db.add(row)
        new_rows.append(row)
    db.flush()
    reused = 0
    for row in new_rows:
        if row.content_hash in reusable:
            db.add(DocumentEmbedding(chunk_id=row.id, model=model, content_hash=row.content_hash,
                                     embedding=reusable[row.content_hash]))
            reused += 1
        else:
            to_embed.append(row)
    for i in range(0, len(to_embed), 64):
        batch = to_embed[i : i + 64]
        vecs = embeddings.embed_texts([(r.section + "\n" + r.text) for r in batch])
        for r, v in zip(batch, vecs):
            db.add(DocumentEmbedding(chunk_id=r.id, model=model, content_hash=r.content_hash, embedding=v))
    db.flush()
    return {"chunks": len(new_rows), "embeddings_reused": reused, "embeddings_generated": len(to_embed), "embedding_model": model}


def regenerate_embeddings(db: Session, doc: Document) -> dict:
    model = embeddings.model_name()
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc.id).order_by(DocumentChunk.chunk_index)).all()
    db.execute(delete(DocumentEmbedding).where(DocumentEmbedding.chunk_id.in_([c.id for c in chunks]), DocumentEmbedding.model == model))
    for i in range(0, len(chunks), 64):
        batch = chunks[i : i + 64]
        for r, v in zip(batch, embeddings.embed_texts([(r.section + "\n" + r.text) for r in batch])):
            db.add(DocumentEmbedding(chunk_id=r.id, model=model, content_hash=r.content_hash, embedding=v))
    db.commit()
    return {"chunks": len(chunks), "embedding_model": model}


def import_doi(db: Session, owner_id: uuid.UUID, doi: str, **kwargs) -> Document:
    d = normalize_doi(doi)
    if not d:
        raise ValueError("Invalid DOI")
    rec = None
    try:
        rec = clients.crossref_work(d)
        source = "crossref"
    except ScholarlyError:
        rec = clients.openalex_work(d)  # raises if not found anywhere
        source = "openalex"
    meta = {"authors": [a.name for a in rec.authors], "year": rec.year, "abstract": rec.abstract, "doi": d,
            "url": rec.url, "venue": rec.venue, "_source": source}
    doc = create_document(db, owner_id, data=None, title=rec.title, source_kind="doi",
                          visibility=Visibility.public_source.value, metadata=meta, **kwargs)
    doc.metadata_verified = True
    doc.status = "ready"
    doc.status_detail = "Metadata record only — no full text stored. Upload the PDF if you have legitimate access."
    doc.extra = {**(doc.extra or {}), "is_oa": rec.is_oa, "oa_url": rec.oa_url}
    if rec.abstract:
        # index the abstract so the record is searchable; clearly marked as abstract-only
        _index_abstract(db, doc, rec.abstract)
    return doc


def _index_abstract(db: Session, doc: Document, abstract: str) -> None:
    v = DocumentVersion(document_id=doc.id, version_no=1, filename="", mime_type="text/plain", extracted_text=abstract,
                        change_note="Abstract from registry", processing_status="ready")
    db.add(v)
    db.flush()
    from app.services.extraction import Block

    reindex_version(db, doc, v, [Block("Abstract", None, True, 1), Block(abstract)])
    doc.current_version_id = v.id


def fetch_url(url: str, max_bytes: int) -> tuple[bytes, str, str]:
    if not re.match(r"^https?://", url):
        raise ValueError("Only http(s) URLs are supported")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60,
                      headers={"User-Agent": "ResearchLab/1.0 (academic research assistant)"}) as r:
        if r.status_code >= 400:
            raise ValueError(f"URL returned HTTP {r.status_code}")
        ctype = r.headers.get("content-type", "").split(";")[0]
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError("Remote file exceeds the upload size limit")
    name = url.rstrip("/").rsplit("/", 1)[-1][:100] or "page"
    if ctype == "text/html":
        html = bytes(buf).decode("utf-8", "ignore")
        html = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html)
        title = re.search(r"(?is)<title>(.*?)</title>", html)
        body = re.sub(r"(?s)<[^>]+>", "\n", html)
        body = re.sub(r"\n\s*\n+", "\n\n", body)
        text = ((title.group(1).strip() + "\n\n") if title else "") + body
        return text.encode(), name + ".txt", "text/plain"
    if ctype == "application/pdf" and not name.lower().endswith(".pdf"):
        name += ".pdf"
    return bytes(buf), name, ctype


def document_to_dict(doc: Document, with_versions: bool = False) -> dict:
    d = {
        "id": str(doc.id), "title": doc.title, "authors": doc.authors, "year": doc.year, "abstract": doc.abstract,
        "doi": doc.doi, "url": doc.url, "venue": doc.venue, "doc_type": doc.doc_type, "source_kind": doc.source_kind,
        "visibility": doc.visibility, "knowledge_category": doc.knowledge_category, "is_own_work": doc.is_own_work,
        "status": doc.status, "status_detail": doc.status_detail, "project_id": str(doc.project_id) if doc.project_id else None,
        "metadata_source": doc.metadata_source, "metadata_verified": doc.metadata_verified,
        "current_version_id": str(doc.current_version_id) if doc.current_version_id else None,
        "duplicate_of_id": str(doc.duplicate_of_id) if doc.duplicate_of_id else None, "extra": doc.extra,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        "deleted_at": doc.deleted_at.isoformat() if doc.deleted_at else None,
    }
    if with_versions:
        d["versions"] = [{
            "id": str(v.id), "version_no": v.version_no, "filename": v.filename, "size_bytes": v.size_bytes,
            "content_hash": v.content_hash, "page_count": v.page_count, "change_note": v.change_note,
            "processing_status": v.processing_status, "headings": v.headings, "extracted_metadata": v.extracted_metadata,
            "references_count": len(v.extracted_references or []),
            "created_at": v.created_at.isoformat() if v.created_at else None,
        } for v in doc.versions]
    return d


_ = YEAR_RE
