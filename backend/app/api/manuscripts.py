from __future__ import annotations

import difflib
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, owned, owned_project, parse_uuid
from app.models import (
    Citation,
    GenerationJob,
    Manuscript,
    ManuscriptSection,
    ManuscriptVersion,
    ResearchDecision,
    ResearcherProfile,
    User,
)
from app.services import citations as cit
from app.services import export
from app.services import manuscript as ms
from app.services.audit import audit
from app.services.jobs import enqueue

router = APIRouter(prefix="/manuscripts", tags=["manuscripts"])


class SectionPlanIn(BaseModel):
    key: str | None = None
    heading: str
    target_words: int = Field(ge=0, le=50000)
    objective: str = ""


class ManuscriptIn(BaseModel):
    project_id: str
    title: str = Field(min_length=3)
    manuscript_type: str = "empirical_article"
    target_word_count: int = Field(default=7000, ge=500, le=60000)
    citation_style: str = Field(default="apa7")
    mode: str = Field(default="guided", pattern="^(guided|complete)$")
    discipline: str = ""
    target_journal: str = ""
    count_references_in_words: bool = False
    requirements: dict = {}
    sections: list[SectionPlanIn] | None = None


class ManuscriptPatch(BaseModel):
    title: str | None = None
    target_journal: str | None = None
    citation_style: str | None = None
    requirements: dict | None = None
    mode: str | None = Field(default=None, pattern="^(guided|complete)$")
    count_references_in_words: bool | None = None


class SectionEditIn(BaseModel):
    content: str | None = None
    heading: str | None = None
    target_words: int | None = Field(default=None, ge=0, le=50000)
    objective: str | None = None
    outline: list | None = None


class SectionDecisionIn(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|revised|regenerate|deferred|instruction)$")
    rationale: str = ""
    instructions: str = ""


class GenerateIn(BaseModel):
    pause_after_outline: bool = False
    keep_existing: bool = True
    regenerate_sections: list[str] = []
    force_outline: bool = False


class AllocationIn(BaseModel):
    target_word_count: int = Field(ge=500, le=60000)
    allocations: dict[str, int] | None = None  # key -> words; otherwise proportional


def _iso(d):
    return d.isoformat() if d else None


def sec_out(s: ManuscriptSection) -> dict:
    return {"id": str(s.id), "key": s.key, "heading": s.heading, "position": s.position, "target_words": s.target_words,
            "objective": s.objective, "outline": s.outline, "evidence_plan": s.evidence_plan, "content": s.content,
            "word_count": s.word_count, "status": s.status, "origin": s.origin, "outstanding_requirements": s.outstanding_requirements,
            "evidence_used": s.evidence_used, "approved_at": _iso(s.approved_at), "content_version": s.content_version, "updated_at": _iso(s.updated_at)}


def ms_out(m: Manuscript, full: bool = False) -> dict:
    secs = sorted(m.sections, key=lambda s: s.position)
    d = {"id": str(m.id), "project_id": str(m.project_id), "title": m.title, "manuscript_type": m.manuscript_type, "discipline": m.discipline,
         "target_journal": m.target_journal, "target_word_count": m.target_word_count, "citation_style": m.citation_style,
         "language_variant": m.language_variant, "mode": m.mode, "status": m.status, "requirements": m.requirements,
         "count_references_in_words": m.count_references_in_words, "created_at": _iso(m.created_at), "updated_at": _iso(m.updated_at),
         "total_words": sum(s.word_count for s in secs if s.key != "abstract"),
         "progress": {"approved": sum(1 for s in secs if s.status == "approved"), "drafted": sum(1 for s in secs if s.content), "sections": len(secs)}}
    if full:
        d["sections"] = [sec_out(s) for s in secs]
        d["quality_report"] = m.quality_report
    return d


@router.get("/structures")
def structures(target_word_count: int = 7000, user: User = CurrentUser) -> dict:
    return {k: ms.allocate(target_word_count, v) for k, v in ms.STRUCTURES.items()} | {"_citation_styles": cit.STYLES}


@router.get("")
def list_manuscripts(project_id: str | None = None, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    q = select(Manuscript).where(Manuscript.owner_id == user.id)
    if project_id:
        q = q.where(Manuscript.project_id == parse_uuid(project_id))
    return [ms_out(m) for m in db.scalars(q.order_by(Manuscript.updated_at.desc())).all()]


@router.post("")
def create(body: ManuscriptIn, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, body.project_id, user)
    if body.citation_style not in cit.STYLES:
        raise HTTPException(422, f"citation_style must be one of {list(cit.STYLES)}")
    try:
        m = ms.create_manuscript(db, user.id, p.id, title=body.title, manuscript_type=body.manuscript_type,
                                 target_word_count=body.target_word_count, citation_style=body.citation_style, mode=body.mode,
                                 sections=[s.model_dump() for s in body.sections] if body.sections else None, discipline=body.discipline,
                                 target_journal=body.target_journal, requirements=body.requirements,
                                 count_references_in_words=body.count_references_in_words)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    audit(db, user.id, "manuscript.create", "manuscript", m.id, project_id=p.id)
    db.commit()
    db.refresh(m)
    return ms_out(m, full=True)


@router.get("/{mid}")
def get(mid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    m.last_accessed_at = datetime.now(timezone.utc)
    db.commit()
    out = ms_out(m, full=True)
    jobs = db.scalars(select(GenerationJob).where(GenerationJob.manuscript_id == m.id).order_by(GenerationJob.created_at.desc()).limit(10)).all()
    out["jobs"] = [{"id": str(j.id), "job_type": j.job_type, "status": j.status, "progress": j.progress, "error": j.error,
                    "created_at": _iso(j.created_at)} for j in jobs]
    return out


@router.patch("/{mid}")
def patch(mid: str, body: ManuscriptPatch, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    for k, v in body.model_dump(exclude_none=True).items():
        if k == "citation_style" and v not in cit.STYLES:
            raise HTTPException(422, "Unknown citation style")
        setattr(m, k, v)
    db.commit()
    return ms_out(m, full=True)


@router.delete("/{mid}")
def delete(mid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    audit(db, user.id, "manuscript.delete", "manuscript", m.id, project_id=m.project_id, details={"title": m.title})
    db.delete(m)
    db.commit()
    return {"ok": True}


@router.post("/{mid}/allocation")
def reallocate(mid: str, body: AllocationIn, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    m.target_word_count = body.target_word_count
    secs = sorted(m.sections, key=lambda s: s.position)
    if body.allocations:
        for s in secs:
            if s.key in body.allocations:
                s.target_words = int(body.allocations[s.key])
    else:
        cur = sum(s.target_words for s in secs if s.key != "abstract") or 1
        for s in secs:
            if s.key != "abstract":
                s.target_words = int(round(s.target_words * body.target_word_count / cur / 10) * 10)
    db.commit()
    return ms_out(m, full=True)


@router.post("/{mid}/sections")
def add_section(mid: str, body: SectionPlanIn, position: int | None = None, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    key = (body.key or body.heading.lower().replace(" ", "_"))[:60]
    if any(s.key == key for s in m.sections):
        raise HTTPException(409, "Section key exists")
    pos = position if position is not None else len(m.sections)
    for s in m.sections:
        if s.position >= pos:
            s.position += 1
    db.add(ManuscriptSection(manuscript_id=m.id, key=key, heading=body.heading, position=pos, target_words=body.target_words, objective=body.objective))
    db.commit()
    db.refresh(m)
    return ms_out(m, full=True)


def _section(db, user, mid, key) -> tuple[Manuscript, ManuscriptSection]:
    m = owned(db, Manuscript, mid, user)
    s = next((x for x in m.sections if x.key == key), None)
    if not s:
        raise HTTPException(404, "Section not found")
    return m, s


@router.put("/{mid}/sections/{key}")
def edit_section(mid: str, key: str, body: SectionEditIn, user: User = CurrentUser, db: Session = DB) -> dict:
    """Direct researcher editing. Edits to approved sections return them to 'revised' draft status."""
    m, s = _section(db, user, mid, key)
    if body.content is not None and body.content != s.content:
        s.content = body.content
        s.word_count = ms.count_words(body.content)
        s.origin = "mixed" if s.origin == "ai" else s.origin
        s.content_version += 1
        if s.status == "approved":
            s.status = "draft"
            s.approved_at = None
        elif s.status == "planned":
            s.status, s.origin = "draft", "researcher"
        ms.sync_citations(db, m, s)
    for k in ("heading", "target_words", "objective", "outline"):
        v = getattr(body, k)
        if v is not None:
            setattr(s, k, v)
    db.commit()
    return sec_out(s)


@router.delete("/{mid}/sections/{key}")
def delete_section(mid: str, key: str, user: User = CurrentUser, db: Session = DB) -> dict:
    m, s = _section(db, user, mid, key)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/{mid}/sections/{key}/decision")
def section_decision(mid: str, key: str, body: SectionDecisionIn, user: User = CurrentUser, db: Session = DB) -> dict:
    m, s = _section(db, user, mid, key)
    job = None
    if body.decision == "approved":
        if not s.content:
            raise HTTPException(400, "Cannot approve an empty section")
        s.status, s.approved_at = "approved", datetime.now(timezone.utc)
    elif body.decision in ("rejected", "revised"):
        s.status = "needs_revision"
    elif body.decision in ("regenerate", "instruction"):
        job = enqueue(db, user.id, "section_generate", {"section_key": s.key, "instructions": body.instructions}, title=f"Write: {s.heading}",
                      project_id=m.project_id, manuscript_id=m.id, max_attempts=2)
    db.add(ResearchDecision(owner_id=user.id, project_id=m.project_id, decision_type="manuscript_section", subject=f"{m.title} — {s.heading}",
                            decision=body.decision, rationale=body.rationale, instructions=body.instructions, entity_type="manuscript_section",
                            entity_id=s.id, payload={"content_version": s.content_version, "word_count": s.word_count}))
    db.commit()
    return {"section": sec_out(s), "job_id": str(job.id) if job else None}


# --- Generation -----------------------------------------------------------------
@router.post("/{mid}/outline")
def outline(mid: str, force: bool = False, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    job = enqueue(db, user.id, "manuscript_outline", {"force": force}, title=f"Outline: {m.title[:60]}", project_id=m.project_id, manuscript_id=m.id, max_attempts=2)
    db.commit()
    return {"job_id": str(job.id)}


@router.post("/{mid}/sections/{key}/generate")
def generate_section(mid: str, key: str, instructions: str = "", user: User = CurrentUser, db: Session = DB) -> dict:
    m, s = _section(db, user, mid, key)
    warn = None
    if m.mode == "guided":
        earlier = [x for x in m.sections if x.position < s.position and x.key != "abstract" and x.status != "approved"]
        if earlier and s.key != "abstract":
            warn = f"Guided mode: earlier sections not yet approved: {', '.join(x.heading for x in earlier)}"
    job = enqueue(db, user.id, "section_generate", {"section_key": s.key, "instructions": instructions}, title=f"Write: {s.heading}",
                  project_id=m.project_id, manuscript_id=m.id, max_attempts=2)
    db.commit()
    return {"job_id": str(job.id), "warning": warn}


@router.post("/{mid}/generate")
def generate_complete(mid: str, body: GenerateIn, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    active = db.scalar(select(GenerationJob).where(GenerationJob.manuscript_id == m.id, GenerationJob.job_type == "manuscript_generate",
                                                   GenerationJob.status.in_(["queued", "running", "paused", "cancel_requested", "awaiting_approval"])))
    if active:
        raise HTTPException(409, f"A generation job already exists for this manuscript ({active.status}); resume or pause it instead. job_id={active.id}")
    job = enqueue(db, user.id, "manuscript_generate", body.model_dump(), title=f"Complete manuscript: {m.title[:60]}", project_id=m.project_id,
                  manuscript_id=m.id, max_attempts=3)
    audit(db, user.id, "manuscript.generate", "manuscript", m.id, project_id=m.project_id, details=body.model_dump())
    db.commit()
    return {"job_id": str(job.id)}


@router.post("/{mid}/audit")
def audit_citations(mid: str, alignment: bool = True, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    if not alignment:  # deterministic-only audit runs synchronously
        rep = ms.audit_manuscript(db, m, alignment=False)
        m.quality_report = {**(m.quality_report or {}), "citation_audit": rep, "word_allocation": ms.check_word_allocation(m)}
        db.commit()
        return {"report": rep}
    job = enqueue(db, user.id, "manuscript_audit", {"alignment": True}, title=f"Citation audit: {m.title[:60]}", project_id=m.project_id, manuscript_id=m.id)
    db.commit()
    return {"job_id": str(job.id)}


@router.post("/{mid}/review")
def review(mid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    job = enqueue(db, user.id, "manuscript_review", {}, title=f"Critical review: {m.title[:60]}", project_id=m.project_id, manuscript_id=m.id)
    db.commit()
    return {"job_id": str(job.id)}


@router.get("/{mid}/word-allocation")
def word_allocation(mid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    return ms.check_word_allocation(owned(db, Manuscript, mid, user))


@router.get("/{mid}/citations")
def citations(mid: str, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    m = owned(db, Manuscript, mid, user)
    rows = db.scalars(select(Citation).where(Citation.section_id.in_([s.id for s in m.sections]))).all()
    keyof = {s.id: s.key for s in m.sections}
    return [{"id": str(c.id), "section": keyof.get(c.section_id), "citekey": c.citekey, "claim": c.claim_text, "locator": c.locator,
             "is_quotation": c.is_quotation, "alignment_status": c.alignment_status, "alignment_note": c.alignment_note,
             "supporting_evidence": c.supporting_evidence, "resolved": c.reference_id is not None} for c in rows]


# --- Versions -----------------------------------------------------------------
@router.get("/{mid}/versions")
def versions(mid: str, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    m = owned(db, Manuscript, mid, user)
    return [{"id": str(v.id), "version_no": v.version_no, "label": v.label, "total_words": v.total_words, "created_by": v.created_by,
             "created_at": _iso(v.created_at)} for v in db.scalars(select(ManuscriptVersion).where(ManuscriptVersion.manuscript_id == m.id)
                                                                   .order_by(ManuscriptVersion.version_no.desc())).all()]


@router.post("/{mid}/versions")
def save_version(mid: str, label: str = "Researcher snapshot", user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    v = ms.snapshot(db, m, label, created_by="researcher")
    db.commit()
    return {"id": str(v.id), "version_no": v.version_no}


@router.get("/{mid}/versions/{vid}")
def get_version(mid: str, vid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    v = db.get(ManuscriptVersion, parse_uuid(vid))
    if not v or v.manuscript_id != m.id:
        raise HTTPException(404, "Version not found")
    return {"id": str(v.id), "version_no": v.version_no, "label": v.label, "snapshot": v.snapshot, "created_at": _iso(v.created_at)}


@router.get("/{mid}/compare")
def compare(mid: str, a: str, b: str = "current", user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)

    def load(x):
        if x == "current":
            return {s.key: (s.heading, s.content or "") for s in m.sections}
        v = db.get(ManuscriptVersion, parse_uuid(x))
        if not v or v.manuscript_id != m.id:
            raise HTTPException(404, "Version not found")
        return {s["key"]: (s["heading"], s.get("content") or "") for s in v.snapshot.get("sections", [])}

    A, B = load(a), load(b)
    out = []
    for key in list(dict.fromkeys(list(A) + list(B))):
        ta, tb = A.get(key, ("", ""))[1], B.get(key, ("", ""))[1]
        if ta == tb:
            out.append({"key": key, "heading": (B.get(key) or A.get(key))[0], "changed": False})
            continue
        diff = list(difflib.unified_diff(ta.split("\n"), tb.split("\n"), lineterm="", n=1))
        out.append({"key": key, "heading": (B.get(key) or A.get(key))[0], "changed": True, "diff": diff[:400],
                    "words_a": ms.count_words(ta), "words_b": ms.count_words(tb)})
    return {"a": a, "b": b, "sections": out}


@router.post("/{mid}/versions/{vid}/restore")
def restore_version(mid: str, vid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    m = owned(db, Manuscript, mid, user)
    v = db.get(ManuscriptVersion, parse_uuid(vid))
    if not v or v.manuscript_id != m.id:
        raise HTTPException(404, "Version not found")
    ms.snapshot(db, m, f"Auto-snapshot before restoring v{v.version_no}", created_by="researcher")
    by_key = {s["key"]: s for s in v.snapshot.get("sections", [])}
    for s in m.sections:
        if s.key in by_key:
            s.content = by_key[s.key].get("content") or ""
            s.word_count = ms.count_words(s.content)
            s.status = "draft" if s.content else "planned"
            s.content_version += 1
            ms.sync_citations(db, m, s)
    audit(db, user.id, "manuscript.restore_version", "manuscript", m.id, project_id=m.project_id, details={"version": v.version_no})
    db.commit()
    return ms_out(m, full=True)


# --- Rendering & export -------------------------------------------------------
@router.get("/{mid}/render")
def render(mid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    return ms.rendered(db, owned(db, Manuscript, mid, user))


@router.get("/{mid}/export/{fmt}")
def export_manuscript(mid: str, fmt: str, user: User = CurrentUser, db: Session = DB):
    m = owned(db, Manuscript, mid, user)
    doc = ms.rendered(db, m)
    prof = db.scalar(select(ResearcherProfile).where(ResearcherProfile.user_id == user.id))
    author = (prof.full_name if prof else "") or user.display_name
    safe = "".join(c if c.isalnum() else "_" for c in m.title)[:60]
    audit(db, user.id, "manuscript.export", "manuscript", m.id, project_id=m.project_id, details={"format": fmt})
    db.commit()
    if fmt == "docx":
        return Response(export.to_docx(doc, author, prof.affiliation if prof else ""),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{safe}.docx"'})
    if fmt == "tex":
        return Response(export.to_latex(doc, author), media_type="application/x-tex", headers={"Content-Disposition": f'attachment; filename="{safe}.tex"'})
    if fmt == "md":
        return Response(export.to_markdown(doc), media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{safe}.md"'})
    raise HTTPException(422, "Format must be docx, tex or md")


_ = func
