"""Hybrid retrieval: PostgreSQL full-text + pgvector similarity + metadata filters,
fused with Reciprocal Rank Fusion. Every hit carries full provenance."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    CollectionDocument,
    Document,
    DocumentChunk,
    DocumentEmbedding,
    Publication,
    ResearchNote,
    Visibility,
)
from app.services import embeddings

RRF_K = 60
_STOP = {"what", "which", "who", "whom", "how", "why", "when", "where", "do", "does", "did", "i", "my", "me", "we", "our", "you",
         "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are", "was", "were", "be", "about", "know", "currently",
         "with", "that", "this", "these", "those", "there", "any", "have", "has", "from", "by", "as", "at", "it", "its", "can"}


def ts_query(query: str):
    """websearch syntax when the researcher uses operators/quotes; otherwise OR over content terms
    (ts_rank_cd still ranks passages matching more terms higher)."""
    if '"' in query or re.search(r"\b(OR|AND)\b|(^|\s)-\w", query):
        return func.websearch_to_tsquery("english", query)
    terms = [t for t in re.findall(r"[A-Za-z0-9]+", query.lower()) if t not in _STOP and len(t) > 1][:30]
    if not terms:
        return func.websearch_to_tsquery("english", query)
    return func.to_tsquery("english", " | ".join(terms))


@dataclass
class SearchFilters:
    collection_ids: list[uuid.UUID] = field(default_factory=list)
    project_id: uuid.UUID | None = None
    year_from: int | None = None
    year_to: int | None = None
    doc_types: list[str] = field(default_factory=list)
    knowledge_categories: list[str] = field(default_factory=list)
    document_ids: list[uuid.UUID] = field(default_factory=list)
    own_work_only: bool = False
    exclude_private: bool = False


def _doc_conditions(owner_id: uuid.UUID, f: SearchFilters):
    conds = [Document.owner_id == owner_id, Document.deleted_at.is_(None)]
    if f.year_from:
        conds.append(Document.year >= f.year_from)
    if f.year_to:
        conds.append(Document.year <= f.year_to)
    if f.doc_types:
        conds.append(Document.doc_type.in_(f.doc_types))
    if f.knowledge_categories:
        conds.append(Document.knowledge_category.in_(f.knowledge_categories))
    if f.document_ids:
        conds.append(Document.id.in_(f.document_ids))
    if f.own_work_only:
        conds.append(Document.is_own_work.is_(True))
    if f.exclude_private:
        conds.append(Document.visibility != Visibility.private_unpublished.value)
    if f.project_id:
        conds.append(Document.project_id == f.project_id)
    if f.collection_ids:
        conds.append(Document.id.in_(select(CollectionDocument.document_id).where(
            CollectionDocument.collection_id.in_(f.collection_ids))))
    return conds


def hybrid_search(db: Session, owner_id: uuid.UUID, query: str, filters: SearchFilters | None = None,
                  k: int = 10, mode: str = "hybrid") -> list[dict]:
    f = filters or SearchFilters()
    conds = _doc_conditions(owner_id, f)
    pool = max(k * 4, 30)
    ranked: dict[uuid.UUID, dict] = {}

    if mode in ("hybrid", "fulltext"):
        tsq = ts_query(query)
        rank = func.ts_rank_cd(DocumentChunk.tsv, tsq)
        rows = db.execute(
            select(DocumentChunk.id, rank.label("r"))
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(and_(*conds), DocumentChunk.tsv.op("@@")(tsq))
            .order_by(rank.desc()).limit(pool)
        ).all()
        for i, (cid, r) in enumerate(rows):
            ranked.setdefault(cid, {"fts_rank": None, "vec_rank": None})
            ranked[cid].update(fts_rank=i + 1, fts_score=float(r))

    if mode in ("hybrid", "semantic"):
        qv = embeddings.embed_query(query)
        dist = DocumentEmbedding.embedding.cosine_distance(qv)
        rows = db.execute(
            select(DocumentChunk.id, dist.label("d"))
            .join(DocumentEmbedding, DocumentEmbedding.chunk_id == DocumentChunk.id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(and_(*conds), DocumentEmbedding.model == embeddings.model_name())
            .order_by(dist).limit(pool)
        ).all()
        for i, (cid, d) in enumerate(rows):
            ranked.setdefault(cid, {"fts_rank": None, "vec_rank": None})
            ranked[cid].update(vec_rank=i + 1, similarity=round(1 - float(d), 4))

    for v in ranked.values():
        v["score"] = sum(1.0 / (RRF_K + r) for r in (v.get("fts_rank"), v.get("vec_rank")) if r)
    top = sorted(ranked.items(), key=lambda kv: kv[1]["score"], reverse=True)[:k]
    if not top:
        return []
    chunks = {c.id: c for c in db.scalars(select(DocumentChunk).where(DocumentChunk.id.in_([t[0] for t in top]))).all()}
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_({c.document_id for c in chunks.values()}))).all()}
    out = []
    for cid, sc in top:
        c = chunks[cid]
        d = docs[c.document_id]
        out.append({
            "chunk_id": str(c.id), "document_id": str(d.id), "title": d.title, "authors": d.authors, "year": d.year,
            "doi": d.doi, "section": c.section, "page_start": c.page_start, "page_end": c.page_end, "text": c.text,
            "knowledge_category": d.knowledge_category, "visibility": d.visibility, "is_own_work": d.is_own_work,
            "basis": "abstract" if d.source_kind == "doi" else "full_text",
            "scores": {k2: v2 for k2, v2 in sc.items()},
        })
    return out


def search_notes(db: Session, owner_id: uuid.UUID, query: str, project_id: uuid.UUID | None = None, k: int = 10) -> list[dict]:
    tsq = ts_query(query)
    q = select(ResearchNote, func.ts_rank_cd(ResearchNote.tsv, tsq).label("r")).where(
        ResearchNote.owner_id == owner_id, ResearchNote.tsv.op("@@")(tsq))
    if project_id:
        q = q.where(ResearchNote.project_id == project_id)
    rows = db.execute(q.order_by(func.ts_rank_cd(ResearchNote.tsv, tsq).desc()).limit(k)).all()
    return [{"note_id": str(n.id), "title": n.title, "body": n.body, "note_type": n.note_type,
             "knowledge_category": n.knowledge_category, "tags": n.tags, "score": float(r),
             "created_at": n.created_at.isoformat()} for n, r in rows]


def search_publications(db: Session, owner_id: uuid.UUID, query: str, k: int = 20) -> list[Publication]:
    tsv = func.to_tsvector("english", func.coalesce(Publication.title, "") + " " + func.coalesce(Publication.abstract, ""))
    tsq = ts_query(query)
    return list(db.scalars(
        select(Publication).where(Publication.owner_id == owner_id, or_(tsv.op("@@")(tsq), Publication.title.ilike(f"%{query[:100]}%")))
        .order_by(func.ts_rank_cd(tsv, tsq).desc()).limit(k)
    ).all())
