from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, ip
from app.config import get_settings
from app.models import ResearcherProfile, User, WritingProfile
from app.security import COOKIE_NAME, create_token, hash_password, rate_limiter, verify_password
from app.services.audit import audit
from app.services.knowledge import ensure_system_collections

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(default="", max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


def _set_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(COOKIE_NAME, token, httponly=True, secure=s.cookie_secure, samesite=s.cookie_samesite,
                    max_age=s.access_token_minutes * 60, path="/")


def _user_out(u: User) -> dict:
    return {"id": str(u.id), "email": u.email, "display_name": u.display_name}


@router.get("/status")
def status(db: Session = DB) -> dict:
    n = db.scalar(select(func.count(User.id))) or 0
    return {"setup_required": n == 0, "registration_open": n == 0 or get_settings().allow_registration}


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response, db: Session = DB) -> dict:
    if not rate_limiter.check(f"register:{ip(request)}", get_settings().login_rate_limit_per_minute):
        raise HTTPException(429, "Too many attempts")
    n = db.scalar(select(func.count(User.id))) or 0
    if n > 0 and not get_settings().allow_registration:
        raise HTTPException(403, "Registration is closed (single-researcher deployment)")
    if db.scalar(select(User).where(func.lower(User.email) == body.email.lower())):
        raise HTTPException(409, "Email already registered")
    u = User(email=body.email.lower(), password_hash=hash_password(body.password), display_name=body.display_name)
    db.add(u)
    db.flush()
    db.add(ResearcherProfile(user_id=u.id, full_name=body.display_name))
    db.add(WritingProfile(owner_id=u.id))
    ensure_system_collections(db, u.id)
    audit(db, u.id, "auth.register", "user", u.id, ip=ip(request))
    db.commit()
    _set_cookie(response, create_token(u))
    return {"user": _user_out(u)}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = DB) -> dict:
    if not rate_limiter.check(f"login:{ip(request)}", get_settings().login_rate_limit_per_minute):
        raise HTTPException(429, "Too many login attempts; wait a minute")
    u = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if not u or not u.is_active or not verify_password(body.password, u.password_hash):
        audit(db, u.id if u else None, "auth.login_failed", "user", u.id if u else "", ip=ip(request), details={"email": body.email.lower()})
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    u.last_login_at = datetime.now(timezone.utc)
    audit(db, u.id, "auth.login", "user", u.id, ip=ip(request))
    db.commit()
    _set_cookie(response, create_token(u))
    return {"user": _user_out(u)}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/logout-all")
def logout_all(response: Response, user: User = CurrentUser, db: Session = DB) -> dict:
    user.token_version += 1
    audit(db, user.id, "auth.logout_all", "user", user.id)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordIn, response: Response, user: User = CurrentUser, db: Session = DB) -> dict:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    audit(db, user.id, "auth.password_changed", "user", user.id)
    db.commit()
    _set_cookie(response, create_token(user))
    return {"ok": True}


@router.get("/me")
def me(user: User = CurrentUser) -> dict:
    return {"user": _user_out(user)}
