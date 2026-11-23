"""ResearchLab API entrypoint."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import analysis, auth, knowledge, literature, manuscripts, profile, projects, research, system
from app.config import get_settings
from app.security import client_ip, rate_limiter
from app.services.embeddings import EmbeddingError
from app.services.jobs import Worker
from app.services.llm.base import LLMError, LLMQuotaExceeded
from app.services.storage import StorageError

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("researchlab")


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker = None
    if settings.run_embedded_worker and settings.environment != "test":
        worker = Worker()
        worker.start()
    yield
    if worker:
        worker.stop()


app = FastAPI(title="ResearchLab API", version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs" if settings.environment != "production" else None, openapi_url="/api/openapi.json")

app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "Authorization"])


@app.middleware("http")
async def guard(request: Request, call_next):
    rid = uuid.uuid4().hex[:12]
    t0 = time.monotonic()
    if request.url.path.startswith("/api/") and not request.url.path.endswith("/health"):
        if not rate_limiter.check(f"api:{client_ip(request)}", settings.rate_limit_per_minute):
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
    try:
        resp = await call_next(request)
    except Exception:  # pragma: no cover - logged and converted to 500
        log.exception("unhandled error rid=%s path=%s", rid, request.url.path)
        return JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
    resp.headers["X-Request-ID"] = rid
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    log.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, resp.status_code, (time.monotonic() - t0) * 1000, rid)
    return resp


@app.exception_handler(LLMQuotaExceeded)
async def quota_handler(_: Request, exc: LLMQuotaExceeded):
    return JSONResponse({"detail": f"AI provider quota exceeded: {exc}. Configure a fallback provider (e.g. Ollama) or retry later.",
                         "code": "ai_quota"}, status_code=503)


@app.exception_handler(LLMError)
async def llm_handler(_: Request, exc: LLMError):
    return JSONResponse({"detail": str(exc), "code": "ai_unavailable"}, status_code=503)


@app.exception_handler(EmbeddingError)
async def emb_handler(_: Request, exc: EmbeddingError):
    return JSONResponse({"detail": str(exc), "code": "embedding_unavailable"}, status_code=503)


@app.exception_handler(StorageError)
async def storage_handler(_: Request, exc: StorageError):
    return JSONResponse({"detail": str(exc), "code": "storage_error"}, status_code=502)


for r in (auth.router, profile.router, projects.router, projects.notes_router, knowledge.router, literature.router, research.router,
          manuscripts.router, analysis.router, system.router):
    app.include_router(r, prefix="/api")
