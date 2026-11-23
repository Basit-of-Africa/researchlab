from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, owned, owned_project
from app.api.projects import idea_out, note_out
from app.models import (
    KnowledgeCategory,
    ResearchIdea,
    ResearchNote,
    User,
    WorkspaceMessage,
    WorkspaceThread,
)
from app.services.agents.core import AGENTS
from app.services.agents.workflows import workspace_reply
from app.services.audit import audit
from app.services.jobs import enqueue
from app.services.llm.base import LLMError, Message

router = APIRouter(prefix="/research", tags=["research"])


class IdeaIn(BaseModel):
    statement: str = Field(min_length=10, max_length=10000)
    context: str = Field(default="", max_length=10000)
    project_id: str | None = None
    use_openalex: bool = True


class IdeaReviseIn(BaseModel):
    statement: str = Field(min_length=10)
    researcher_response: str = ""
    reassess: bool = True


class ChallengeIn(BaseModel):
    argument: str = Field(min_length=10, max_length=20000)
    project_id: str | None = None
    use_openalex: bool = True


class TheoryIn(BaseModel):
    problem: str = Field(min_length=10)
    project_id: str
    candidate_theories: list[str] = []
    use_openalex: bool = True


class MethodIn(BaseModel):
    problem: str = Field(min_length=10)
    project_id: str
    preferences: str = ""


class ThreadIn(BaseModel):
    title: str = "New conversation"
    project_id: str | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    agent: str = "strategist"
    use_openalex: bool = False


class PromoteIn(BaseModel):
    knowledge_category: str = KnowledgeCategory.provisional_opinion.value
    note_type: str = "observation"
    title: str = ""


@router.get("/agents")
def agents(user: User = CurrentUser) -> list[dict]:
    return [{"key": a.key, "name": a.name, "role": a.role, "tools": a.tools} for a in AGENTS.values()]


@router.get("/ideas")
def list_ideas(project_id: str | None = None, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    q = select(ResearchIdea).where(ResearchIdea.owner_id == user.id)
    if project_id:
        q = q.where(ResearchIdea.project_id == owned_project(db, project_id, user).id)
    return [idea_out(i) for i in db.scalars(q.order_by(ResearchIdea.created_at.desc())).all()]


@router.post("/ideas")
def explore_idea(body: IdeaIn, user: User = CurrentUser, db: Session = DB) -> dict:
    pid = owned_project(db, body.project_id, user).id if body.project_id else None
    idea = ResearchIdea(owner_id=user.id, project_id=pid, statement=body.statement, researcher_context=body.context)
    db.add(idea)
    db.flush()
    job = enqueue(db, user.id, "idea_assessment", {"idea_id": str(idea.id), "use_openalex": body.use_openalex},
                  title="Research idea assessment", project_id=pid, max_attempts=2)
    audit(db, user.id, "idea.explore", "research_idea", idea.id, project_id=pid)
    db.commit()
    return {"idea": idea_out(idea), "job_id": str(job.id)}


@router.get("/ideas/{idea_id}")
def get_idea(idea_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    return idea_out(owned(db, ResearchIdea, idea_id, user))


@router.post("/ideas/{idea_id}/revise")
def revise_idea(idea_id: str, body: IdeaReviseIn, user: User = CurrentUser, db: Session = DB) -> dict:
    """The researcher challenges/refines the assessment; a new version is created (history preserved)."""
    old = owned(db, ResearchIdea, idea_id, user)
    old.researcher_response = body.researcher_response
    old.status = "revised"
    new = ResearchIdea(owner_id=user.id, project_id=old.project_id, statement=body.statement,
                       researcher_context=(old.researcher_context + "\n\nResearcher's response to the previous assessment:\n" + body.researcher_response).strip(),
                       parent_id=old.id, version=old.version + 1)
    db.add(new)
    db.flush()
    job = None
    if body.reassess:
        job = enqueue(db, user.id, "idea_assessment", {"idea_id": str(new.id)}, title="Research idea re-assessment", project_id=old.project_id, max_attempts=2)
    db.commit()
    return {"idea": idea_out(new), "job_id": str(job.id) if job else None}


@router.post("/challenge")
def challenge(body: ChallengeIn, user: User = CurrentUser, db: Session = DB) -> dict:
    pid = owned_project(db, body.project_id, user).id if body.project_id else None
    job = enqueue(db, user.id, "challenge", {"argument": body.argument, "use_openalex": body.use_openalex}, title="Research Challenge Mode",
                  project_id=pid, max_attempts=2)
    db.commit()
    return {"job_id": str(job.id)}


@router.post("/theory")
def theory(body: TheoryIn, user: User = CurrentUser, db: Session = DB) -> dict:
    pid = owned_project(db, body.project_id, user).id
    job = enqueue(db, user.id, "theory_analysis", body.model_dump(exclude={"project_id"}), title="Theoretical analysis", project_id=pid, max_attempts=2)
    db.commit()
    return {"job_id": str(job.id)}


@router.post("/methodology")
def methodology(body: MethodIn, user: User = CurrentUser, db: Session = DB) -> dict:
    pid = owned_project(db, body.project_id, user).id
    job = enqueue(db, user.id, "method_advice", body.model_dump(exclude={"project_id"}), title="Methodology advice", project_id=pid, max_attempts=2)
    db.commit()
    return {"job_id": str(job.id)}


# ---------------------------------------------------------------------------
# Research workspace conversations
# ---------------------------------------------------------------------------
def _msg_out(m: WorkspaceMessage) -> dict:
    return {"id": str(m.id), "role": m.role, "agent": m.agent, "content": m.content, "sources": m.sources,
            "knowledge_category": m.knowledge_category, "promoted_to": m.promoted_to, "created_at": m.created_at.isoformat()}


@router.get("/threads")
def list_threads(project_id: str | None = None, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    q = select(WorkspaceThread).where(WorkspaceThread.owner_id == user.id)
    if project_id:
        q = q.where(WorkspaceThread.project_id == owned_project(db, project_id, user).id)
    return [{"id": str(t.id), "title": t.title, "project_id": str(t.project_id) if t.project_id else None, "updated_at": t.updated_at.isoformat()}
            for t in db.scalars(q.order_by(WorkspaceThread.updated_at.desc()).limit(100)).all()]


@router.post("/threads")
def create_thread(body: ThreadIn, user: User = CurrentUser, db: Session = DB) -> dict:
    t = WorkspaceThread(owner_id=user.id, title=body.title, project_id=owned_project(db, body.project_id, user).id if body.project_id else None)
    db.add(t)
    db.commit()
    return {"id": str(t.id), "title": t.title}


@router.get("/threads/{tid}")
def get_thread(tid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    t = owned(db, WorkspaceThread, tid, user)
    msgs = db.scalars(select(WorkspaceMessage).where(WorkspaceMessage.thread_id == t.id).order_by(WorkspaceMessage.created_at)).all()
    return {"id": str(t.id), "title": t.title, "project_id": str(t.project_id) if t.project_id else None, "messages": [_msg_out(m) for m in msgs]}


@router.delete("/threads/{tid}")
def delete_thread(tid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    db.delete(owned(db, WorkspaceThread, tid, user))
    db.commit()
    return {"ok": True}


@router.post("/threads/{tid}/messages")
def send_message(tid: str, body: MessageIn, user: User = CurrentUser, db: Session = DB) -> dict:
    t = owned(db, WorkspaceThread, tid, user)
    if body.agent not in AGENTS:
        raise HTTPException(422, "Unknown agent")
    prior = db.scalars(select(WorkspaceMessage).where(WorkspaceMessage.thread_id == t.id).order_by(WorkspaceMessage.created_at.desc()).limit(10)).all()
    history = [Message("user" if m.role == "researcher" else "assistant", m.content[:4000]) for m in reversed(prior)]
    um = WorkspaceMessage(thread_id=t.id, role="researcher", content=body.content)
    db.add(um)
    if t.title == "New conversation":
        t.title = body.content[:80]
    db.commit()
    try:
        res = workspace_reply(db, user.id, body.agent, body.content, history, project_id=t.project_id, use_openalex=body.use_openalex)
    except LLMError as e:
        raise HTTPException(503, str(e)) from e
    am = WorkspaceMessage(thread_id=t.id, role="agent", agent=body.agent, content=res["text"], sources=res["sources"],
                          knowledge_category=KnowledgeCategory.ai_interpretation.value)
    db.add(am)
    db.commit()
    return {"researcher_message": _msg_out(um), "reply": _msg_out(am), "excluded_private": res["excluded_private"], "warnings": res["warnings"]}


@router.post("/messages/{mid}/promote")
def promote_message(mid: str, body: PromoteIn, user: User = CurrentUser, db: Session = DB) -> dict:
    """Conversation is not knowledge. Only explicitly promoted statements become research notes."""
    m = db.get(WorkspaceMessage, mid)
    t = db.get(WorkspaceThread, m.thread_id) if m else None
    if not m or not t or t.owner_id != user.id:
        raise HTTPException(404, "Message not found")
    cat = body.knowledge_category
    if m.role == "agent" and cat not in (KnowledgeCategory.ai_interpretation.value,):
        cat = KnowledgeCategory.ai_interpretation.value  # AI output stays labelled as AI interpretation
    n = ResearchNote(owner_id=user.id, project_id=t.project_id, title=body.title or m.content[:80], body=m.content, note_type=body.note_type,
                     knowledge_category=cat, anchor={"thread_id": str(t.id), "message_id": str(m.id), "sources": m.sources[:20]})
    db.add(n)
    db.flush()
    m.promoted_to = {"note_id": str(n.id), "knowledge_category": cat}
    audit(db, user.id, "workspace.promote", "research_note", n.id, project_id=t.project_id)
    db.commit()
    return note_out(n)
