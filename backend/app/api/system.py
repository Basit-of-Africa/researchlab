from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, owned
from app.config import get_settings
from app.models import (
    AuditLog,
    DiscoveryQueueItem,
    Document,
    GenerationJob,
    Manuscript,
    Publication,
    ResearchNote,
    ResearchProject,
    User,
)
from app.services import embeddings
from app.services.agents.core import data_policy, set_setting
from app.services.jobs import request_pause, resume
from app.services.llm.router import provider_status
from app.services.storage import get_storage

router = APIRouter(tags=["system"])


def job_out(j: GenerationJob, steps: bool = False) -> dict:
    d = {"id": str(j.id), "job_type": j.job_type, "title": j.title, "status": j.status, "progress": j.progress, "error": j.error,
         "attempts": j.attempts, "max_attempts": j.max_attempts, "result": j.result, "params": j.params,
         "project_id": str(j.project_id) if j.project_id else None, "manuscript_id": str(j.manuscript_id) if j.manuscript_id else None,
         "created_at": j.created_at.isoformat(), "started_at": j.started_at.isoformat() if j.started_at else None,
         "finished_at": j.finished_at.isoformat() if j.finished_at else None, "updated_at": j.updated_at.isoformat() if j.updated_at else None}
    if steps:
        d["steps"] = [{"key": s.step_key, "label": s.label, "status": s.status, "error": s.error, "attempts": s.attempts,
                       "started_at": s.started_at.isoformat() if s.started_at else None,
                       "finished_at": s.finished_at.isoformat() if s.finished_at else None} for s in sorted(j.steps, key=lambda s: s.position)]
    return d


@router.get("/jobs")
def list_jobs(status: str | None = None, project_id: str | None = None, limit: int = 50, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    q = select(GenerationJob).where(GenerationJob.owner_id == user.id)
    if status:
        q = q.where(GenerationJob.status.in_(status.split(",")))
    if project_id:
        q = q.where(GenerationJob.project_id == project_id)
    return [job_out(j) for j in db.scalars(q.order_by(GenerationJob.created_at.desc()).limit(min(limit, 200))).all()]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    return job_out(owned(db, GenerationJob, job_id, user), steps=True)


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    j = owned(db, GenerationJob, job_id, user)
    request_pause(db, j)
    db.commit()
    return job_out(j)


class ResumeIn(BaseModel):
    params: dict = {}


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, body: ResumeIn | None = None, user: User = CurrentUser, db: Session = DB) -> dict:
    j = owned(db, GenerationJob, job_id, user)
    if j.status not in ("paused", "failed", "awaiting_approval"):
        raise HTTPException(409, f"Job is {j.status}; only paused, failed or awaiting-approval jobs can be resumed")
    extra = dict((body.params if body else {}) or {})
    if j.status == "awaiting_approval":
        extra["outline_approved"] = True
    resume(db, j, extra)
    db.commit()
    return job_out(j)


@router.get("/dashboard")
def dashboard(user: User = CurrentUser, db: Session = DB) -> dict:
    projects = db.scalars(select(ResearchProject).where(ResearchProject.owner_id == user.id, ResearchProject.status == "active")
                          .order_by(func.coalesce(ResearchProject.last_accessed_at, ResearchProject.updated_at).desc()).limit(8)).all()
    mss = db.scalars(select(Manuscript).where(Manuscript.owner_id == user.id).order_by(func.coalesce(Manuscript.last_accessed_at, Manuscript.updated_at).desc()).limit(6)).all()
    docs = db.scalars(select(Document).where(Document.owner_id == user.id, Document.deleted_at.is_(None)).order_by(Document.created_at.desc()).limit(8)).all()
    notes = db.scalars(select(ResearchNote).where(ResearchNote.owner_id == user.id).order_by(ResearchNote.updated_at.desc()).limit(6)).all()
    jobs = db.scalars(select(GenerationJob).where(GenerationJob.owner_id == user.id, GenerationJob.status.in_(
        ["queued", "running", "paused", "cancel_requested", "awaiting_approval", "failed"])).order_by(GenerationJob.created_at.desc()).limit(12)).all()
    activity = db.scalars(select(AuditLog).where(AuditLog.user_id == user.id).order_by(AuditLog.created_at.desc()).limit(15)).all()
    pending_q = db.scalar(select(func.count(DiscoveryQueueItem.id)).where(DiscoveryQueueItem.owner_id == user.id, DiscoveryQueueItem.status == "pending"))
    return {
        "projects": [{"id": str(p.id), "title": p.title, "updated_at": p.updated_at.isoformat()} for p in projects],
        "manuscripts": [{"id": str(m.id), "title": m.title, "status": m.status, "project_id": str(m.project_id),
                         "words": sum(s.word_count for s in m.sections if s.key != "abstract"), "target": m.target_word_count} for m in mss],
        "recent_documents": [{"id": str(d.id), "title": d.title or "(untitled)", "status": d.status, "created_at": d.created_at.isoformat(),
                              "knowledge_category": d.knowledge_category} for d in docs],
        "recent_notes": [{"id": str(n.id), "title": n.title or n.body[:80], "knowledge_category": n.knowledge_category,
                          "updated_at": n.updated_at.isoformat()} for n in notes],
        "jobs": [job_out(j) for j in jobs],
        "activity": [{"action": a.action, "entity_type": a.entity_type, "at": a.created_at.isoformat()} for a in activity],
        "stats": {
            "documents": db.scalar(select(func.count(Document.id)).where(Document.owner_id == user.id, Document.deleted_at.is_(None))),
            "publications": db.scalar(select(func.count(Publication.id)).where(Publication.owner_id == user.id)),
            "notes": db.scalar(select(func.count(ResearchNote.id)).where(ResearchNote.owner_id == user.id)),
            "projects": db.scalar(select(func.count(ResearchProject.id)).where(ResearchProject.owner_id == user.id, ResearchProject.status == "active")),
            "pending_reviews": pending_q,
        },
    }


class PolicyIn(BaseModel):
    allow_external_private: bool


@router.get("/settings")
def get_settings_view(user: User = CurrentUser, db: Session = DB) -> dict:
    s = get_settings()
    return {
        "ai_providers": provider_status(),
        "ai_data_policy": data_policy(db, user.id),
        "embedding": {"provider": s.embedding_provider, "model": embeddings.model_name(), "dimension": s.embedding_dim,
                      "external": embeddings.is_external(),
                      "note": "The hashing provider is a lexical fallback; configure a semantic model for better retrieval." if s.embedding_provider == "hashing" else ""},
        "storage": {"backend": s.storage_backend, "warning": "Local storage on an ephemeral host (e.g. Render free) is lost on restart. Use Supabase Storage in production."
                    if s.storage_backend == "local" and s.environment == "production" else ""},
        "scholarly": {"openalex": True, "crossref": True, "semantic_scholar": {"api_key": bool(s.semantic_scholar_api_key)},
                      "core": {"configured": bool(s.core_api_key)}, "contact_email_set": bool(s.scholarly_contact_email)},
        "limits": {"max_upload_mb": s.max_upload_mb, "analysis_timeout_seconds": s.analysis_timeout_seconds},
    }


@router.put("/settings/ai-data-policy")
def set_policy(body: PolicyIn, user: User = CurrentUser, db: Session = DB) -> dict:
    set_setting(db, user.id, "ai_data_policy", {"allow_external_private": body.allow_external_private})
    from app.services.audit import audit

    audit(db, user.id, "settings.ai_data_policy", "app_setting", "", details=body.model_dump())
    db.commit()
    return data_policy(db, user.id)


@router.get("/health")
def health(db: Session = DB) -> dict:
    ok_db = True
    vec = None
    try:
        db.execute(text("SELECT 1"))
        vec = db.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
    except Exception:  # noqa: BLE001
        ok_db = False
    try:
        st = get_storage().healthy()
    except Exception:  # noqa: BLE001
        st = False
    return {"status": "ok" if ok_db and st else "degraded", "database": ok_db, "pgvector": vec, "storage": st}
