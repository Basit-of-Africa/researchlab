"""Object storage abstraction.

* ``local``    — filesystem; suitable for development or a persistent disk.
                 NOT suitable for ephemeral free-tier hosts (Render free).
* ``supabase`` — Supabase Storage REST API (free tier: 1 GB).

Originals are written once under a content-addressed key and never silently
overwritten; a replaced document gets a new version and a new key.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

import httpx

from app.config import get_settings


class StorageError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_name(name: str) -> str:
    base = os.path.basename(name or "file")
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:120]
    return base or "file"


def make_key(owner_id: uuid.UUID, area: str, filename: str, content_hash: str) -> str:
    return f"{owner_id}/{area}/{content_hash[:16]}-{uuid.uuid4().hex[:8]}-{_safe_name(filename)}"


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root)):
            raise StorageError("Invalid storage key")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        if p.exists():
            raise StorageError("Refusing to overwrite an existing object")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise StorageError(f"Object not found: {key}")
        return p.read_bytes()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def local_path(self, key: str) -> Path:
        return self._path(key)

    def healthy(self) -> bool:
        return self.root.exists() and os.access(self.root, os.W_OK)


class SupabaseStorage:
    def __init__(self, url: str, service_key: str, bucket: str) -> None:
        if not url or not service_key:
            raise StorageError("SUPABASE_URL and SUPABASE_SERVICE_KEY are required for the supabase backend")
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        r = httpx.post(
            f"{self.base}/object/{self.bucket}/{key}",
            content=data,
            headers={**self.headers, "Content-Type": content_type, "x-upsert": "false"},
            timeout=120,
        )
        if r.status_code >= 300:
            raise StorageError(f"Supabase upload failed ({r.status_code}): {r.text[:200]}")

    def get(self, key: str) -> bytes:
        r = httpx.get(f"{self.base}/object/{self.bucket}/{key}", headers=self.headers, timeout=120)
        if r.status_code >= 300:
            raise StorageError(f"Supabase download failed ({r.status_code})")
        return r.content

    def delete(self, key: str) -> None:
        r = httpx.request(
            "DELETE", f"{self.base}/object/{self.bucket}", json={"prefixes": [key]}, headers=self.headers, timeout=60
        )
        if r.status_code >= 300:
            raise StorageError(f"Supabase delete failed ({r.status_code})")

    def healthy(self) -> bool:
        try:
            r = httpx.get(f"{self.base}/bucket/{self.bucket}", headers=self.headers, timeout=10)
            return r.status_code < 300
        except httpx.HTTPError:
            return False


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "supabase":
            _storage = SupabaseStorage(s.supabase_url, s.supabase_service_key, s.supabase_bucket)
        else:
            _storage = LocalStorage(s.local_storage_path)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
