"""Persisting discovered publications and verifying bibliographic metadata."""
from __future__ import annotations

import difflib
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Publication, PublicationAuthor, SourceVerificationRecord
from app.services.scholarly import clients
from app.services.scholarly.clients import PubRecord, ScholarlyError, normalize_doi


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def title_similarity(a: str, b: str) -> float:
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_existing(db: Session, owner_id: uuid.UUID, rec: PubRecord) -> Publication | None:
    conds = []
    if rec.doi:
        conds.append(Publication.doi == rec.doi)
    if rec.openalex_id:
        conds.append(Publication.openalex_id == rec.openalex_id)
    if rec.s2_id:
        conds.append(Publication.s2_id == rec.s2_id)
    if conds:
        p = db.scalar(select(Publication).where(Publication.owner_id == owner_id, or_(*conds)))
        if p:
            return p
    if rec.title and rec.year:
        cands = db.scalars(
            select(Publication).where(Publication.owner_id == owner_id, Publication.year == rec.year).limit(500)
        ).all()
        for c in cands:
            if title_similarity(c.title, rec.title) > 0.95:
                return c
    return None


def upsert_publication(db: Session, owner_id: uuid.UUID, rec: PubRecord) -> Publication:
    """Insert or enrich a publication. Existing non-empty fields are never overwritten
    with values from a different source; only gaps are filled."""
    pub = find_existing(db, owner_id, rec)
    is_new = pub is None
    if pub is None:
        pub = Publication(owner_id=owner_id, title=rec.title or "(untitled)", source_db=rec.source_db, raw={})
        db.add(pub)
    for f in (
        "year", "publication_date", "venue", "publisher", "volume", "issue", "pages", "doi", "openalex_id",
        "s2_id", "core_id", "url", "pub_type", "oa_url", "cited_by_count",
    ):
        val = getattr(rec, f)
        if val not in (None, "") and getattr(pub, f) in (None, ""):
            setattr(pub, f, val)
    if rec.abstract and not pub.abstract:
        pub.abstract, pub.abstract_source = rec.abstract, rec.abstract_source
    if rec.is_oa is not None and pub.is_oa is None:
        pub.is_oa = rec.is_oa
    pub.full_text_available = bool(pub.full_text_available or rec.full_text_available)
    if rec.concepts and not pub.concepts:
        pub.concepts = rec.concepts
    raw = dict(pub.raw or {})
    raw[rec.source_db] = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "referenced_works": rec.referenced_works[:100]}
    pub.raw = raw
    if is_new or not pub.authors:
        db.flush()
        if not is_new:
            pub.authors.clear()
        for i, a in enumerate(rec.authors):
            pub.authors.append(
                PublicationAuthor(
                    position=i, name=a.name[:400], given=a.given, family=a.family, orcid=a.orcid,
                    openalex_author_id=a.openalex_author_id, institution=a.institution,
                )
            )
    db.flush()
    return pub


def publication_to_dict(p: Publication) -> dict:
    return {
        "id": str(p.id),
        "title": p.title,
        "authors": [{"name": a.name, "orcid": a.orcid, "institution": a.institution} for a in p.authors],
        "year": p.year,
        "venue": p.venue,
        "publisher": p.publisher,
        "volume": p.volume,
        "issue": p.issue,
        "pages": p.pages,
        "abstract": p.abstract,
        "abstract_source": p.abstract_source,
        "doi": p.doi,
        "openalex_id": p.openalex_id,
        "s2_id": p.s2_id,
        "url": p.url,
        "pub_type": p.pub_type,
        "is_oa": p.is_oa,
        "oa_url": p.oa_url,
        "full_text_available": p.full_text_available,
        "cited_by_count": p.cited_by_count,
        "concepts": p.concepts,
        "source_db": p.source_db,
        "verification_status": p.verification_status,
        "verified_at": p.verified_at.isoformat() if p.verified_at else None,
        "credibility_note": p.credibility_note,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _family(name: str) -> str:
    name = name.strip()
    if "," in name:
        return name.split(",")[0].strip().lower()
    return (name.split()[-1] if name.split() else "").lower()


def compare_metadata(claimed: dict, rec: PubRecord) -> dict:
    """Compare claimed bibliographic fields with an authoritative record."""
    issues = []
    sim = title_similarity(claimed.get("title", ""), rec.title) if claimed.get("title") else None
    if sim is not None and sim < 0.85:
        issues.append({"field": "title", "claimed": claimed.get("title"), "registry": rec.title, "similarity": round(sim, 3)})
    cy = claimed.get("year")
    if cy and rec.year and int(cy) != int(rec.year):
        issues.append({"field": "year", "claimed": cy, "registry": rec.year})
    ca = [_family(a if isinstance(a, str) else (a.get("family") or a.get("name") or "")) for a in claimed.get("authors") or []]
    ra = [(a.family or _family(a.name)).lower() for a in rec.authors]
    if ca and ra and ca[0] and ca[0] not in ra[:3]:
        issues.append({"field": "first_author", "claimed": ca[0], "registry": ra[:3]})
    return {"title_similarity": sim, "issues": issues}


def verify_bibliographic(db: Session, owner_id: uuid.UUID, claimed: dict, *, reference_id=None, publication_id=None,
                         document_id=None, allow_title_search: bool = True) -> dict:
    """Verify a claimed reference against Crossref (DOI) and OpenAlex/Crossref (title).

    Returns {status, record, comparison, checks}. Status values:
      verified       — registry record found and key fields agree
      mismatch       — registry record found but fields disagree (or DOI points elsewhere)
      not_found      — DOI does not resolve / no plausible match
      incomplete     — not enough metadata to verify
      unverifiable   — registries unreachable (network/quota); retry later
    """
    checks: list[dict] = []
    doi = normalize_doi(claimed.get("doi"))
    rec: PubRecord | None = None
    status = "incomplete"
    comparison: dict = {}

    def record(check_type: str, st: str, source: str, details: dict) -> None:
        checks.append({"check": check_type, "status": st, "source": source, **details})
        db.add(SourceVerificationRecord(
            owner_id=owner_id, reference_id=reference_id, publication_id=publication_id, document_id=document_id,
            check_type=check_type, status=st, source=source, details=details,
        ))

    if doi:
        try:
            rec = clients.crossref_work(doi)
            comparison = compare_metadata(claimed, rec)
            status = "verified" if not comparison["issues"] else "mismatch"
            record("doi_resolve", status, "crossref", {"doi": doi, **comparison})
        except ScholarlyError as e:
            if "not found" in str(e):
                try:  # some DOIs (e.g. DataCite) are not in Crossref; OpenAlex covers many
                    rec = clients.openalex_work(doi)
                    comparison = compare_metadata(claimed, rec)
                    status = "verified" if not comparison["issues"] else "mismatch"
                    record("doi_resolve", status, "openalex", {"doi": doi, **comparison})
                except ScholarlyError as e2:
                    status = "not_found" if "not found" in str(e2) else "unverifiable"
                    record("doi_resolve", status, "crossref+openalex", {"doi": doi, "error": str(e2)})
            else:
                status = "unverifiable"
                record("doi_resolve", status, "crossref", {"doi": doi, "error": str(e)})
    if rec is None and status != "unverifiable" and allow_title_search and claimed.get("title"):
        try:
            cands = clients.crossref_search(claimed["title"], rows=5, bibliographic=True)
            best = max(cands, key=lambda r: title_similarity(r.title, claimed["title"]), default=None)
            if best and title_similarity(best.title, claimed["title"]) >= 0.9:
                rec = best
                comparison = compare_metadata(claimed, best)
                status = "verified" if not comparison["issues"] else "mismatch"
                if doi and best.doi and best.doi != doi:
                    comparison["issues"].append({"field": "doi", "claimed": doi, "registry": best.doi})
                    status = "mismatch"
            else:
                status = "not_found"
            record("title_search", status, "crossref", {"title": claimed["title"], "best_match": best.title if best else None, **comparison})
        except ScholarlyError as e:
            status = "unverifiable"
            record("title_search", status, "crossref", {"error": str(e)})
    return {"status": status, "record": rec.to_dict() if rec else None, "comparison": comparison, "checks": checks}


def verify_publication(db: Session, pub: Publication) -> dict:
    claimed = {"title": pub.title, "year": pub.year, "doi": pub.doi, "authors": [a.name for a in pub.authors]}
    res = verify_bibliographic(db, pub.owner_id, claimed, publication_id=pub.id)
    pub.verification_status = res["status"]
    pub.verified_at = datetime.now(timezone.utc)
    if res["record"] and res["status"] in ("verified",):
        r = res["record"]
        for f in ("venue", "publisher", "volume", "issue", "pages", "pub_type"):
            if r.get(f) and not getattr(pub, f):
                setattr(pub, f, r[f])
        if r.get("doi") and not pub.doi:
            pub.doi = r["doi"]
    return res
