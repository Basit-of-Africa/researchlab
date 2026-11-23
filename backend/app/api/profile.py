from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, owned, parse_uuid
from app.models import Document, ResearcherProfile, ResearchInterest, User, WritingProfile
from app.services.audit import audit
from app.services.jobs import enqueue
from app.services.style import describe_metrics

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=200)
    affiliation: str | None = Field(default=None, max_length=500)
    orcid: str | None = Field(default=None, max_length=50)
    bio: str | None = None
    qualifications: list[dict] | None = None
    disciplines: list[str] | None = None
    methodological_experience: list[str] | None = None
    theoretical_preferences: list[str] | None = None
    referencing_style: str | None = None
    language_variant: str | None = Field(default=None, pattern=r"^en-(GB|US)$")
    writing_conventions: str | None = None
    research_history_summary: str | None = None


class InterestIn(BaseModel):
    label: str = Field(min_length=2, max_length=300)
    description: str = ""
    keywords: list[str] = []
    is_active: bool = True


class WritingProfileIn(BaseModel):
    name: str | None = None
    sample_document_ids: list[str] | None = None
    researcher_overrides: dict | None = None
    status: str | None = Field(default=None, pattern="^(draft|reviewed)$")


def _profile(db: Session, user: User) -> ResearcherProfile:
    p = db.scalar(select(ResearcherProfile).where(ResearcherProfile.user_id == user.id))
    if not p:
        p = ResearcherProfile(user_id=user.id)
        db.add(p)
        db.flush()
    return p


def _p_out(p: ResearcherProfile) -> dict:
    return {k: getattr(p, k) for k in ("full_name", "title", "affiliation", "orcid", "bio", "qualifications", "disciplines",
                                        "methodological_experience", "theoretical_preferences", "referencing_style", "language_variant",
                                        "writing_conventions", "research_history_summary")} | {"updated_at": p.updated_at.isoformat() if p.updated_at else None}


@router.get("")
def get_profile(user: User = CurrentUser, db: Session = DB) -> dict:
    p = _profile(db, user)
    db.commit()
    interests = db.scalars(select(ResearchInterest).where(ResearchInterest.owner_id == user.id).order_by(ResearchInterest.created_at)).all()
    return {"profile": _p_out(p), "interests": [_i_out(i) for i in interests]}


@router.put("")
def update_profile(body: ProfileIn, user: User = CurrentUser, db: Session = DB) -> dict:
    p = _profile(db, user)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(p, k, v)
    audit(db, user.id, "profile.update", "researcher_profile", p.id, details={"fields": list(changes)})
    db.commit()
    return {"profile": _p_out(p)}


def _i_out(i: ResearchInterest) -> dict:
    return {"id": str(i.id), "label": i.label, "description": i.description, "keywords": i.keywords, "is_active": i.is_active}


@router.post("/interests")
def add_interest(body: InterestIn, user: User = CurrentUser, db: Session = DB) -> dict:
    i = ResearchInterest(owner_id=user.id, **body.model_dump())
    db.add(i)
    db.commit()
    return _i_out(i)


@router.put("/interests/{interest_id}")
def update_interest(interest_id: str, body: InterestIn, user: User = CurrentUser, db: Session = DB) -> dict:
    i = owned(db, ResearchInterest, interest_id, user)
    for k, v in body.model_dump().items():
        setattr(i, k, v)
    db.commit()
    return _i_out(i)


@router.delete("/interests/{interest_id}")
def delete_interest(interest_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    db.delete(owned(db, ResearchInterest, interest_id, user))
    db.commit()
    return {"ok": True}


def _wp_out(wp: WritingProfile) -> dict:
    return {"id": str(wp.id), "name": wp.name, "is_default": wp.is_default, "sample_document_ids": wp.sample_document_ids,
            "metrics": wp.metrics, "metrics_description": describe_metrics(wp.metrics), "qualitative": wp.qualitative,
            "researcher_overrides": wp.researcher_overrides, "version": wp.version, "status": wp.status,
            "updated_at": wp.updated_at.isoformat() if wp.updated_at else None,
            "labels": {"metrics": "Computed from your writing samples", "qualitative": "AI interpretation — review and correct",
                       "researcher_overrides": "Your corrections (take precedence)"}}


@router.get("/writing")
def writing_profiles(user: User = CurrentUser, db: Session = DB) -> list[dict]:
    rows = db.scalars(select(WritingProfile).where(WritingProfile.owner_id == user.id).order_by(WritingProfile.created_at)).all()
    if not rows:
        wp = WritingProfile(owner_id=user.id)
        db.add(wp)
        db.commit()
        rows = [wp]
    return [_wp_out(w) for w in rows]


@router.put("/writing/{profile_id}")
def update_writing(profile_id: str, body: WritingProfileIn, user: User = CurrentUser, db: Session = DB) -> dict:
    wp = owned(db, WritingProfile, profile_id, user)
    if body.sample_document_ids is not None:
        ids = [parse_uuid(i) for i in body.sample_document_ids]
        docs = db.scalars(select(Document).where(Document.id.in_(ids), Document.owner_id == user.id)).all()
        if len(docs) != len(ids):
            raise HTTPException(404, "One or more documents not found")
        wp.sample_document_ids = [str(d.id) for d in docs]
    if body.researcher_overrides is not None:
        wp.researcher_overrides = body.researcher_overrides
        wp.version += 1
    if body.name:
        wp.name = body.name
    if body.status:
        wp.status = body.status
    audit(db, user.id, "writing_profile.update", "writing_profile", wp.id)
    db.commit()
    return _wp_out(wp)


@router.post("/writing/{profile_id}/analyse")
def analyse_writing(profile_id: str, with_ai: bool = True, user: User = CurrentUser, db: Session = DB) -> dict:
    wp = owned(db, WritingProfile, profile_id, user)
    if not wp.sample_document_ids:
        raise HTTPException(400, "Select writing samples (your own documents) first")
    job = enqueue(db, user.id, "writing_style_analysis", {"profile_id": str(wp.id), "with_ai": with_ai}, title="Writing-style analysis")
    db.commit()
    return {"job_id": str(job.id)}
