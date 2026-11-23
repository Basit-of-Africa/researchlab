from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, owned, owned_project, parse_uuid
from app.models import (
    AgentRun,
    ApprovalStatus,
    AuditLog,
    CollectionKind,
    Document,
    KnowledgeCategory,
    KnowledgeCollection,
    Manuscript,
    ResearchDecision,
    ResearcherFeedback,
    ResearchIdea,
    ResearchMethod,
    ResearchNote,
    ResearchProject,
    ResearchQuestion,
    TheoreticalFramework,
    User,
)
from app.services.audit import audit

router = APIRouter(prefix="/projects", tags=["projects"])

DECISIONS = {"approved", "rejected", "revised", "regenerate", "deferred", "instruction"}


class ProjectIn(BaseModel):
    title: str = Field(min_length=2, max_length=500)
    description: str = ""
    objectives: list[str] = []
    discipline: str = ""


class ProjectPatch(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    objectives: list[str] | None = None
    discipline: str | None = None


class QuestionIn(BaseModel):
    text: str = Field(min_length=5)
    kind: str = Field(default="primary", pattern="^(primary|secondary|hypothesis|objective)$")
    rationale: str = ""
    origin: str = Field(default="researcher", pattern="^(researcher|ai)$")


class NoteIn(BaseModel):
    body: str = Field(min_length=1)
    title: str = ""
    note_type: str = Field(default="observation", pattern="^(observation|idea|annotation|theoretical|methodological|question|argument|lesson|feedback)$")
    knowledge_category: str = Field(default=KnowledgeCategory.provisional_opinion.value)
    tags: list[str] = []
    project_id: str | None = None
    document_id: str | None = None
    publication_id: str | None = None
    anchor: dict = {}


class ApprovalIn(BaseModel):
    entity_type: str = Field(pattern="^(research_question|framework|method|idea|gap|argument|manuscript_section|other)$")
    entity_id: str | None = None
    decision: str
    subject: str = ""
    rationale: str = ""
    instructions: str = ""
    revised_text: str | None = None
    payload: dict = {}


class FrameworkIn(BaseModel):
    name: str
    origins: str = ""
    assumptions: list = []
    constructs: list = []
    strengths: list = []
    limitations: list = []
    suitability: str = ""
    model: dict = {}
    hypotheses: list = []


class MethodIn(BaseModel):
    approach: str = ""
    design: str = ""
    population: str = ""
    sampling: str = ""
    sample_size: str = ""
    variables: list = []
    instruments: str = ""
    data_collection: str = ""
    reliability_validity: str = ""
    ethics: str = ""
    analysis_plan: str = ""
    rationale: str = ""


class FeedbackIn(BaseModel):
    target_type: str
    target_id: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    feedback: str = ""
    applies_to_profile: bool = False


def _iso(d):
    return d.isoformat() if d else None


def project_out(p: ResearchProject, db: Session | None = None) -> dict:
    d = {"id": str(p.id), "title": p.title, "description": p.description, "objectives": p.objectives, "discipline": p.discipline,
         "status": p.status, "archived_at": _iso(p.archived_at), "created_at": _iso(p.created_at), "updated_at": _iso(p.updated_at),
         "last_accessed_at": _iso(p.last_accessed_at), "collection_id": str(p.collection_id) if p.collection_id else None}
    if db is not None:
        d["counts"] = {
            "questions": db.scalar(select(func.count(ResearchQuestion.id)).where(ResearchQuestion.project_id == p.id)),
            "notes": db.scalar(select(func.count(ResearchNote.id)).where(ResearchNote.project_id == p.id)),
            "documents": db.scalar(select(func.count(Document.id)).where(Document.project_id == p.id, Document.deleted_at.is_(None))),
            "manuscripts": db.scalar(select(func.count(Manuscript.id)).where(Manuscript.project_id == p.id)),
            "decisions": db.scalar(select(func.count(ResearchDecision.id)).where(ResearchDecision.project_id == p.id)),
        }
    return d


def q_out(q: ResearchQuestion) -> dict:
    return {"id": str(q.id), "text": q.text, "kind": q.kind, "status": q.status, "origin": q.origin, "position": q.position,
            "rationale": q.rationale, "updated_at": _iso(q.updated_at)}


def note_out(n: ResearchNote) -> dict:
    return {"id": str(n.id), "title": n.title, "body": n.body, "note_type": n.note_type, "knowledge_category": n.knowledge_category,
            "tags": n.tags, "anchor": n.anchor, "project_id": str(n.project_id) if n.project_id else None,
            "document_id": str(n.document_id) if n.document_id else None, "publication_id": str(n.publication_id) if n.publication_id else None,
            "created_at": _iso(n.created_at), "updated_at": _iso(n.updated_at)}


def decision_out(d: ResearchDecision) -> dict:
    return {"id": str(d.id), "decision_type": d.decision_type, "subject": d.subject, "decision": d.decision, "rationale": d.rationale,
            "instructions": d.instructions, "payload": d.payload, "entity_type": d.entity_type,
            "entity_id": str(d.entity_id) if d.entity_id else None, "decided_by": d.decided_by, "created_at": _iso(d.created_at)}


def fw_out(f: TheoreticalFramework) -> dict:
    return {"id": str(f.id), "name": f.name, "origins": f.origins, "assumptions": f.assumptions, "constructs": f.constructs,
            "strengths": f.strengths, "limitations": f.limitations, "suitability": f.suitability, "model": f.model,
            "hypotheses": f.hypotheses, "source_refs": f.source_refs, "status": f.status, "origin": f.origin,
            "knowledge_category": f.knowledge_category, "created_at": _iso(f.created_at)}


def method_out(m: ResearchMethod) -> dict:
    return {"id": str(m.id), **{k: getattr(m, k) for k in ("approach", "design", "population", "sampling", "sample_size", "variables",
            "instruments", "data_collection", "reliability_validity", "ethics", "analysis_plan", "rationale", "status", "origin")},
            "created_at": _iso(m.created_at)}


def idea_out(i: ResearchIdea) -> dict:
    return {"id": str(i.id), "statement": i.statement, "researcher_context": i.researcher_context, "assessment": i.assessment,
            "researcher_response": i.researcher_response, "status": i.status, "version": i.version,
            "parent_id": str(i.parent_id) if i.parent_id else None, "project_id": str(i.project_id) if i.project_id else None,
            "created_at": _iso(i.created_at)}


# ---------------------------------------------------------------------------
@router.get("")
def list_projects(status: str = "active", user: User = CurrentUser, db: Session = DB) -> list[dict]:
    q = select(ResearchProject).where(ResearchProject.owner_id == user.id)
    if status != "all":
        q = q.where(ResearchProject.status == status)
    return [project_out(p, db) for p in db.scalars(q.order_by(ResearchProject.updated_at.desc())).all()]


@router.post("")
def create_project(body: ProjectIn, user: User = CurrentUser, db: Session = DB) -> dict:
    p = ResearchProject(owner_id=user.id, **body.model_dump())
    db.add(p)
    db.flush()
    c = KnowledgeCollection(owner_id=user.id, name=f"Project: {body.title[:250]} ({str(p.id)[:6]})", kind=CollectionKind.custom.value,
                            description="Project literature collection")
    db.add(c)
    db.flush()
    p.collection_id = c.id
    audit(db, user.id, "project.create", "research_project", p.id, project_id=p.id)
    db.commit()
    return project_out(p, db)


@router.get("/{project_id}")
def get_project(project_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    p.last_accessed_at = datetime.now(timezone.utc)
    db.commit()
    out = project_out(p, db)
    out["questions"] = [q_out(q) for q in db.scalars(select(ResearchQuestion).where(ResearchQuestion.project_id == p.id).order_by(ResearchQuestion.position, ResearchQuestion.created_at)).all()]
    out["frameworks"] = [fw_out(f) for f in db.scalars(select(TheoreticalFramework).where(TheoreticalFramework.project_id == p.id).order_by(TheoreticalFramework.created_at.desc())).all()]
    out["methods"] = [method_out(m) for m in db.scalars(select(ResearchMethod).where(ResearchMethod.project_id == p.id).order_by(ResearchMethod.created_at.desc())).all()]
    out["ideas"] = [idea_out(i) for i in db.scalars(select(ResearchIdea).where(ResearchIdea.project_id == p.id).order_by(ResearchIdea.created_at.desc())).all()]
    return out


@router.patch("/{project_id}")
def update_project(project_id: str, body: ProjectPatch, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    ch = body.model_dump(exclude_none=True)
    for k, v in ch.items():
        setattr(p, k, v)
    audit(db, user.id, "project.update", "research_project", p.id, project_id=p.id, details={"fields": list(ch)})
    db.commit()
    return project_out(p, db)


@router.post("/{project_id}/archive")
def archive_project(project_id: str, archived: bool = True, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    p.status = "archived" if archived else "active"
    p.archived_at = datetime.now(timezone.utc) if archived else None
    audit(db, user.id, "project.archive" if archived else "project.unarchive", "research_project", p.id, project_id=p.id)
    db.commit()
    return project_out(p, db)


@router.delete("/{project_id}")
def delete_project(project_id: str, confirm: str = "", user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    if confirm != p.title:
        raise HTTPException(400, "To delete, pass ?confirm=<exact project title>. Knowledge-base documents are retained.")
    audit(db, user.id, "project.delete", "research_project", p.id, details={"title": p.title})
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/{project_id}/duplicate")
def duplicate_project(project_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    src = owned_project(db, project_id, user)
    p = ResearchProject(owner_id=user.id, title=f"{src.title} (copy)", description=src.description, objectives=list(src.objectives or []),
                        discipline=src.discipline, collection_id=src.collection_id)
    db.add(p)
    db.flush()
    for q in db.scalars(select(ResearchQuestion).where(ResearchQuestion.project_id == src.id)).all():
        db.add(ResearchQuestion(owner_id=user.id, project_id=p.id, text=q.text, kind=q.kind, status=q.status, origin=q.origin,
                                position=q.position, rationale=q.rationale))
    for n in db.scalars(select(ResearchNote).where(ResearchNote.project_id == src.id)).all():
        db.add(ResearchNote(owner_id=user.id, project_id=p.id, title=n.title, body=n.body, note_type=n.note_type,
                            knowledge_category=n.knowledge_category, tags=n.tags, document_id=n.document_id, anchor=n.anchor))
    for f in db.scalars(select(TheoreticalFramework).where(TheoreticalFramework.project_id == src.id)).all():
        db.add(TheoreticalFramework(owner_id=user.id, project_id=p.id, **{k: getattr(f, k) for k in (
            "name", "origins", "assumptions", "constructs", "strengths", "limitations", "suitability", "model", "hypotheses",
            "source_refs", "status", "origin", "knowledge_category")}))
    for m in db.scalars(select(ResearchMethod).where(ResearchMethod.project_id == src.id)).all():
        db.add(ResearchMethod(owner_id=user.id, project_id=p.id, **{k: getattr(m, k) for k in (
            "approach", "design", "population", "sampling", "sample_size", "variables", "instruments", "data_collection",
            "reliability_validity", "ethics", "analysis_plan", "rationale", "status", "origin")}))
    audit(db, user.id, "project.duplicate", "research_project", p.id, project_id=p.id, details={"source": str(src.id)})
    db.commit()
    return project_out(p, db)


@router.get("/{project_id}/export")
def export_project(project_id: str, user: User = CurrentUser, db: Session = DB):
    p = owned_project(db, project_id, user)
    full = get_project(project_id, user, db)
    full["notes"] = [note_out(n) for n in db.scalars(select(ResearchNote).where(ResearchNote.project_id == p.id)).all()]
    full["decisions"] = [decision_out(d) for d in db.scalars(select(ResearchDecision).where(ResearchDecision.project_id == p.id)).all()]
    mss = []
    for m in db.scalars(select(Manuscript).where(Manuscript.project_id == p.id)).all():
        mss.append({"title": m.title, "sections": [{"heading": s.heading, "content": s.content, "status": s.status} for s in m.sections]})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(full, indent=2, default=str))
        for i, m in enumerate(mss):
            z.writestr(f"manuscripts/{i + 1}.md", "\n\n".join([f"# {m['title']}"] + [f"## {s['heading']}\n\n{s['content']}" for s in m["sections"]]))
    buf.seek(0)
    audit(db, user.id, "project.export", "research_project", p.id, project_id=p.id)
    db.commit()
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="project-{str(p.id)[:8]}.zip"'})


# --- Research questions -----------------------------------------------------
@router.post("/{project_id}/questions")
def add_question(project_id: str, body: QuestionIn, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    pos = (db.scalar(select(func.max(ResearchQuestion.position)).where(ResearchQuestion.project_id == p.id)) or 0) + 1
    q = ResearchQuestion(owner_id=user.id, project_id=p.id, position=pos, status=ApprovalStatus.proposed.value, **body.model_dump())
    db.add(q)
    db.commit()
    return q_out(q)


@router.patch("/{project_id}/questions/{qid}")
def edit_question(project_id: str, qid: str, body: QuestionIn, user: User = CurrentUser, db: Session = DB) -> dict:
    owned_project(db, project_id, user)
    q = owned(db, ResearchQuestion, qid, user)
    if q.status == ApprovalStatus.approved.value and q.text != body.text:
        q.status = ApprovalStatus.revised.value
    q.text, q.kind, q.rationale = body.text, body.kind, body.rationale
    db.commit()
    return q_out(q)


@router.delete("/{project_id}/questions/{qid}")
def delete_question(project_id: str, qid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    owned_project(db, project_id, user)
    db.delete(owned(db, ResearchQuestion, qid, user))
    db.commit()
    return {"ok": True}


# --- Approval gates -----------------------------------------------------------
@router.post("/{project_id}/approvals")
def record_approval(project_id: str, body: ApprovalIn, user: User = CurrentUser, db: Session = DB) -> dict:
    """Every significant decision goes through here. Only the researcher can approve."""
    p = owned_project(db, project_id, user)
    if body.decision not in DECISIONS:
        raise HTTPException(422, f"decision must be one of {sorted(DECISIONS)}")
    subject = body.subject
    status_map = {"approved": "approved", "rejected": "rejected", "revised": "revised", "deferred": "deferred"}
    entity = None
    if body.entity_type == "research_question":
        entity = owned(db, ResearchQuestion, body.entity_id, user)
        if body.revised_text:
            entity.text = body.revised_text
        subject = subject or entity.text
    elif body.entity_type == "framework":
        entity = owned(db, TheoreticalFramework, body.entity_id, user)
        subject = subject or entity.name
        if body.decision in ("approved", "revised"):
            entity.knowledge_category = KnowledgeCategory.established_position.value
    elif body.entity_type == "method":
        entity = owned(db, ResearchMethod, body.entity_id, user)
        subject = subject or f"{entity.approach}: {entity.design[:200]}"
    elif body.entity_type == "idea":
        entity = owned(db, ResearchIdea, body.entity_id, user)
        subject = subject or entity.statement[:300]
        if body.revised_text:
            entity.researcher_response = body.revised_text
    if entity is not None and body.decision in status_map:
        entity.status = status_map[body.decision]
    if not subject:
        raise HTTPException(422, "subject is required for this decision")
    d = ResearchDecision(owner_id=user.id, project_id=p.id, decision_type=body.entity_type, subject=subject[:4000], decision=body.decision,
                         rationale=body.rationale, instructions=body.instructions, payload=body.payload,
                         entity_type=body.entity_type, entity_id=parse_uuid(body.entity_id) if body.entity_id else None)
    db.add(d)
    audit(db, user.id, f"decision.{body.decision}", body.entity_type, body.entity_id or "", project_id=p.id)
    db.commit()
    return decision_out(d)


@router.get("/{project_id}/decisions")
def list_decisions(project_id: str, decision_type: str | None = None, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    p = owned_project(db, project_id, user)
    q = select(ResearchDecision).where(ResearchDecision.project_id == p.id)
    if decision_type:
        q = q.where(ResearchDecision.decision_type == decision_type)
    return [decision_out(d) for d in db.scalars(q.order_by(ResearchDecision.created_at.desc())).all()]


# --- Frameworks & methods (researcher-authored or edited) ----------------------
@router.post("/{project_id}/frameworks")
def add_framework(project_id: str, body: FrameworkIn, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    f = TheoreticalFramework(owner_id=user.id, project_id=p.id, origin="researcher", status="proposed",
                             knowledge_category=KnowledgeCategory.provisional_opinion.value, **body.model_dump())
    db.add(f)
    db.commit()
    return fw_out(f)


@router.put("/{project_id}/frameworks/{fid}")
def edit_framework(project_id: str, fid: str, body: FrameworkIn, user: User = CurrentUser, db: Session = DB) -> dict:
    owned_project(db, project_id, user)
    f = owned(db, TheoreticalFramework, fid, user)
    for k, v in body.model_dump().items():
        setattr(f, k, v)
    if f.origin == "ai":
        f.origin = "mixed"
    if f.status == "approved":
        f.status = "revised"
    db.commit()
    return fw_out(f)


@router.delete("/{project_id}/frameworks/{fid}")
def delete_framework(project_id: str, fid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    owned_project(db, project_id, user)
    db.delete(owned(db, TheoreticalFramework, fid, user))
    db.commit()
    return {"ok": True}


@router.post("/{project_id}/methods")
def add_method(project_id: str, body: MethodIn, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    m = ResearchMethod(owner_id=user.id, project_id=p.id, origin="researcher", status="proposed", **body.model_dump())
    db.add(m)
    db.commit()
    return method_out(m)


@router.put("/{project_id}/methods/{mid}")
def edit_method(project_id: str, mid: str, body: MethodIn, user: User = CurrentUser, db: Session = DB) -> dict:
    owned_project(db, project_id, user)
    m = owned(db, ResearchMethod, mid, user)
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    if m.origin == "ai":
        m.origin = "mixed"
    if m.status == "approved":
        m.status = "revised"
    db.commit()
    return method_out(m)


# --- History & feedback ---------------------------------------------------------
@router.get("/{project_id}/history")
def project_history(project_id: str, limit: int = 200, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    logs = db.scalars(select(AuditLog).where(AuditLog.project_id == p.id).order_by(AuditLog.created_at.desc()).limit(limit)).all()
    runs = db.scalars(select(AgentRun).where(AgentRun.project_id == p.id).order_by(AgentRun.created_at.desc()).limit(limit)).all()
    return {"events": [{"action": l.action, "entity_type": l.entity_type, "entity_id": l.entity_id, "details": l.details, "at": _iso(l.created_at)} for l in logs],
            "agent_runs": [{"id": str(r.id), "agent": r.agent, "task": r.task, "status": r.status, "provider": r.provider, "model": r.model,
                            "duration_ms": r.duration_ms, "excluded_private_sources": r.excluded_private_sources, "error": r.error,
                            "at": _iso(r.created_at)} for r in runs]}


@router.post("/{project_id}/feedback")
def add_feedback(project_id: str, body: FeedbackIn, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    f = ResearcherFeedback(owner_id=user.id, project_id=p.id, target_type=body.target_type,
                           target_id=parse_uuid(body.target_id) if body.target_id else None, rating=body.rating, feedback=body.feedback,
                           applies_to_profile=body.applies_to_profile)
    db.add(f)
    db.commit()
    return {"id": str(f.id)}


# --- Notes (global and project-scoped) ------------------------------------------
notes_router = APIRouter(prefix="/notes", tags=["notes"])


@notes_router.get("")
def list_notes(project_id: str | None = None, knowledge_category: str | None = None, q: str | None = None, limit: int = 100,
               user: User = CurrentUser, db: Session = DB) -> list[dict]:
    stmt = select(ResearchNote).where(ResearchNote.owner_id == user.id)
    if project_id:
        stmt = stmt.where(ResearchNote.project_id == parse_uuid(project_id))
    if knowledge_category:
        stmt = stmt.where(ResearchNote.knowledge_category == knowledge_category)
    if q:
        stmt = stmt.where(ResearchNote.tsv.op("@@")(func.websearch_to_tsquery("english", q)))
    return [note_out(n) for n in db.scalars(stmt.order_by(ResearchNote.updated_at.desc()).limit(min(limit, 500))).all()]


@notes_router.post("")
def create_note(body: NoteIn, user: User = CurrentUser, db: Session = DB) -> dict:
    if body.knowledge_category not in {c.value for c in KnowledgeCategory}:
        raise HTTPException(422, "invalid knowledge_category")
    n = ResearchNote(owner_id=user.id, body=body.body, title=body.title, note_type=body.note_type, knowledge_category=body.knowledge_category,
                     tags=body.tags, anchor=body.anchor)
    if body.project_id:
        n.project_id = owned_project(db, body.project_id, user).id
    if body.document_id:
        n.document_id = owned(db, Document, body.document_id, user).id
    if body.publication_id:
        from app.models import Publication

        n.publication_id = owned(db, Publication, body.publication_id, user).id
    db.add(n)
    audit(db, user.id, "note.create", "research_note", n.id, project_id=n.project_id)
    db.commit()
    return note_out(n)


@notes_router.put("/{note_id}")
def update_note(note_id: str, body: NoteIn, user: User = CurrentUser, db: Session = DB) -> dict:
    n = owned(db, ResearchNote, note_id, user)
    old_cat = n.knowledge_category
    n.body, n.title, n.note_type, n.knowledge_category, n.tags, n.anchor = body.body, body.title, body.note_type, body.knowledge_category, body.tags, body.anchor
    if old_cat != n.knowledge_category:
        audit(db, user.id, "note.recategorised", "research_note", n.id, project_id=n.project_id, details={"from": old_cat, "to": n.knowledge_category})
    db.commit()
    return note_out(n)


@notes_router.delete("/{note_id}")
def delete_note(note_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    n = owned(db, ResearchNote, note_id, user)
    audit(db, user.id, "note.delete", "research_note", n.id, project_id=n.project_id, details={"title": n.title, "body": n.body[:500]})
    db.delete(n)
    db.commit()
    return {"ok": True}
