from __future__ import annotations

import uuid
from typing import TypeVar

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ResearchProject, User
from app.security import client_ip, get_current_user

T = TypeVar("T")

DB = Depends(get_db)
CurrentUser = Depends(get_current_user)


def parse_uuid(v: str | uuid.UUID, what: str = "id") -> uuid.UUID:
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except ValueError:
        raise HTTPException(422, f"Invalid {what}") from None


def owned(db: Session, model: type[T], obj_id, user: User, owner_field: str = "owner_id") -> T:
    """Fetch an entity and enforce ownership (project-level data separation)."""
    obj = db.get(model, parse_uuid(obj_id))
    if obj is None or getattr(obj, owner_field) != user.id:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


def owned_project(db: Session, project_id, user: User) -> ResearchProject:
    return owned(db, ResearchProject, project_id, user)


def ip(request: Request) -> str:
    return client_ip(request)
