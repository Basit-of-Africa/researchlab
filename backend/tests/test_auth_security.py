import uuid

from tests.conftest import PASSWORD


def test_first_run_registration_then_closed(client):
    assert client.get("/api/auth/status").json()["setup_required"] is True
    r = client.post("/api/auth/register", json={"email": "a@example.org", "password": PASSWORD})
    assert r.status_code == 200
    assert client.get("/api/auth/status").json() == {"setup_required": False, "registration_open": False}
    r2 = client.post("/api/auth/register", json={"email": "b@example.org", "password": PASSWORD})
    assert r2.status_code == 403


def test_password_policy(client):
    r = client.post("/api/auth/register", json={"email": "a@example.org", "password": "short"})
    assert r.status_code == 422


def test_authentication_required(client):
    for path in ("/api/projects", "/api/knowledge/documents", "/api/dashboard", "/api/settings", "/api/jobs"):
        assert client.get(path).status_code == 401, path


def test_login_logout_and_revocation(auth_client):
    c = auth_client
    assert c.get("/api/auth/me").status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/login", json={"email": "researcher@example.org", "password": "wrong password!!"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": "researcher@example.org", "password": PASSWORD})
    assert r.status_code == 200
    token = c.cookies.get("rl_session")
    assert token
    c.post("/api/auth/logout-all")
    c.cookies.set("rl_session", token)
    assert c.get("/api/auth/me").status_code == 401  # old token revoked


def test_session_cookie_is_httponly(client):
    r = client.post("/api/auth/register", json={"email": "a@example.org", "password": PASSWORD})
    sc = r.headers.get("set-cookie", "").lower()
    assert "httponly" in sc and "samesite=lax" in sc


def test_api_keys_never_exposed(auth_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "gemini_api_key", "AIzaSECRET-should-never-leak")
    r = auth_client.get("/api/settings")
    assert r.status_code == 200
    assert "AIzaSECRET" not in r.text
    assert "should-never-leak" not in auth_client.get("/api/openapi.json").text


def test_project_isolation_between_researchers(auth_client):
    from app.db import SessionLocal
    from app.models import User
    from app.security import create_token, hash_password

    pid = auth_client.post("/api/projects", json={"title": "Private project"}).json()["id"]
    db = SessionLocal()
    other = User(email="intruder@example.org", password_hash=hash_password(PASSWORD))
    db.add(other)
    db.commit()
    token = create_token(other)
    db.close()
    h = {"Authorization": f"Bearer {token}"}
    auth_client.cookies.clear()
    assert auth_client.get(f"/api/projects/{pid}", headers=h).status_code == 404
    assert auth_client.get("/api/projects", headers=h).json() == []
    assert auth_client.get(f"/api/projects/{uuid.uuid4()}", headers=h).status_code == 404


def test_upload_validation(auth_client):
    c = auth_client
    r = c.post("/api/knowledge/documents/upload", files={"file": ("fake.pdf", b"not really a pdf", "application/pdf")})
    assert r.status_code == 415
    r = c.post("/api/knowledge/documents/upload", files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert r.status_code == 415
    r = c.post("/api/knowledge/documents/upload", files={"file": ("x.docx", b"PK\x03\x04garbage", "application/octet-stream")})
    assert r.status_code == 415
    r = c.post("/api/knowledge/documents/upload", files={"file": ("empty.txt", b"", "text/plain")})
    assert r.status_code == 400


def test_upload_size_limit(auth_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    r = auth_client.post("/api/knowledge/documents/upload", files={"file": ("big.txt", b"a" * (1024 * 1024 + 10), "text/plain")})
    assert r.status_code == 413


def test_health_and_security_headers(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] is True and body["pgvector"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_rate_limit(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 3)
    codes = [client.post("/api/auth/login", json={"email": "x@example.org", "password": "whatever-password"}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and 429 in codes[3:]


def test_audit_log_records_actions(auth_client):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import AuditLog

    auth_client.post("/api/projects", json={"title": "Audited"})
    db = SessionLocal()
    actions = {a.action for a in db.scalars(select(AuditLog)).all()}
    db.close()
    assert {"auth.register", "project.create"} <= actions
