"""Durable, resumable background jobs backed by PostgreSQL.

* Jobs are claimed with ``FOR UPDATE SKIP LOCKED`` and a lease
  (``locked_until``). If a worker dies, the lease expires and another worker
  resumes the job.
* Handlers break work into named *steps*. A step that already succeeded is not
  re-executed on resume — its stored output is returned instead. This is what
  makes long manuscript generation resumable without regenerating completed
  sections.
* A researcher can pause (``cancel_requested``) a running job; the worker stops
  at the next step boundary and marks it ``paused``. Resuming re-queues it.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import GenerationJob, GenerationJobStep, JobStatus
from app.services.llm.base import LLMQuotaExceeded, LLMUnavailable

log = logging.getLogger("researchlab.jobs")

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


class JobPaused(Exception):
    pass


class JobAwaitingApproval(Exception):
    def __init__(self, message: str, payload: dict | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class PermanentJobError(Exception):
    """Errors that retrying will not fix (bad input, missing configuration)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    db: Session,
    owner_id: uuid.UUID,
    job_type: str,
    params: dict | None = None,
    *,
    title: str = "",
    project_id: uuid.UUID | None = None,
    manuscript_id: uuid.UUID | None = None,
    max_attempts: int = 3,
) -> GenerationJob:
    job = GenerationJob(
        owner_id=owner_id,
        job_type=job_type,
        params=params or {},
        title=title or job_type.replace("_", " "),
        project_id=project_id,
        manuscript_id=manuscript_id,
        max_attempts=max_attempts,
        status=JobStatus.queued.value,
    )
    db.add(job)
    db.flush()
    return job


class JobContext:
    def __init__(self, db: Session, job: GenerationJob) -> None:
        self.db = db
        self.job = job
        self.params = job.params or {}
        self._position = 0

    # -- lease / cancellation ---------------------------------------------
    def heartbeat(self) -> None:
        lease = get_settings().job_lease_seconds
        self.db.execute(
            text("UPDATE generation_jobs SET locked_until = :u, updated_at = now() WHERE id = :id"),
            {"u": utcnow() + timedelta(seconds=lease), "id": self.job.id},
        )
        status = self.db.scalar(text("SELECT status FROM generation_jobs WHERE id = :id"), {"id": self.job.id})
        self.db.commit()
        if status == JobStatus.cancel_requested.value:
            raise JobPaused("Paused at researcher's request")

    def progress(self, value: float) -> None:
        self.job.progress = max(0.0, min(1.0, value))
        self.db.commit()

    # -- steps ---------------------------------------------------------------
    def get_step(self, key: str) -> GenerationJobStep | None:
        return self.db.scalar(
            select(GenerationJobStep).where(GenerationJobStep.job_id == self.job.id, GenerationJobStep.step_key == key)
        )

    def plan_steps(self, steps: list[tuple[str, str]]) -> None:
        """Pre-register steps so the UI can show the full plan."""
        for i, (key, label) in enumerate(steps):
            if not self.get_step(key):
                self.db.add(GenerationJobStep(job_id=self.job.id, step_key=key, label=label, position=i))
        self.db.commit()

    def step(self, key: str, label: str, fn: Callable[[], Any], *, force: bool = False) -> Any:
        self._position += 1
        st = self.get_step(key)
        if st and st.status == "succeeded" and not force:
            return (st.output or {}).get("value")
        self.heartbeat()
        if st is None:
            st = GenerationJobStep(job_id=self.job.id, step_key=key, label=label, position=self._position)
            self.db.add(st)
        st.status, st.started_at, st.error = "running", utcnow(), ""
        st.attempts = (st.attempts or 0) + 1
        self.db.commit()
        try:
            value = fn()
        except Exception as e:
            self.db.rollback()
            st = self.get_step(key)
            if st:
                st.status, st.error, st.finished_at = "failed", str(e)[:4000], utcnow()
                self.db.commit()
            raise
        st = self.get_step(key)
        assert st is not None
        st.status, st.output, st.finished_at = "succeeded", {"value": value}, utcnow()
        self.db.commit()
        return value


Handler = Callable[[JobContext], dict | None]
HANDLERS: dict[str, Handler] = {}


def handler(job_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return deco


def claim_next(db: Session, worker_id: str = WORKER_ID) -> uuid.UUID | None:
    lease = get_settings().job_lease_seconds
    row = db.execute(
        text(
            """
            UPDATE generation_jobs SET status = 'running', locked_by = :w, locked_until = :until,
                   started_at = COALESCE(started_at, now()), attempts = attempts + 1, updated_at = now()
            WHERE id = (
                SELECT id FROM generation_jobs
                WHERE (status = 'queued' AND (run_after IS NULL OR run_after <= now()))
                   OR (status = 'running' AND locked_until < now())
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id
            """
        ),
        {"w": worker_id, "until": utcnow() + timedelta(seconds=lease)},
    ).first()
    db.commit()
    return row[0] if row else None


def run_job(job_id: uuid.UUID) -> str:
    """Execute a claimed job. Returns final status."""
    _ensure_handlers_loaded()
    db = SessionLocal()
    try:
        job = db.get(GenerationJob, job_id)
        if job is None:
            return "missing"
        fn = HANDLERS.get(job.job_type)
        if fn is None:
            job.status, job.error, job.finished_at = JobStatus.failed.value, f"No handler for {job.job_type}", utcnow()
            db.commit()
            return job.status
        ctx = JobContext(db, job)
        try:
            result = fn(ctx) or {}
            db.refresh(job)
            job.status, job.result, job.progress = JobStatus.succeeded.value, result, 1.0
            job.error, job.finished_at, job.locked_until = "", utcnow(), None
        except JobPaused as e:
            db.rollback()
            job = db.get(GenerationJob, job_id)
            job.status, job.error, job.locked_until = JobStatus.paused.value, str(e), None
        except JobAwaitingApproval as e:
            db.rollback()
            job = db.get(GenerationJob, job_id)
            job.status, job.result, job.locked_until = JobStatus.awaiting_approval.value, {"message": str(e), **e.payload}, None
        except LLMQuotaExceeded as e:
            db.rollback()
            job = db.get(GenerationJob, job_id)
            _retry_or_fail(job, f"AI quota exceeded: {e}", delay_s=300)
        except (LLMUnavailable, PermanentJobError) as e:
            db.rollback()
            job = db.get(GenerationJob, job_id)
            job.status, job.error, job.finished_at, job.locked_until = JobStatus.failed.value, str(e), utcnow(), None
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.error("job %s failed: %s\n%s", job_id, e, traceback.format_exc())
            job = db.get(GenerationJob, job_id)
            _retry_or_fail(job, f"{type(e).__name__}: {e}", delay_s=30 * (job.attempts or 1))
        db.commit()
        return job.status
    finally:
        db.close()


def _retry_or_fail(job: GenerationJob, msg: str, delay_s: int) -> None:
    job.error = msg[:4000]
    job.locked_until = None
    if (job.attempts or 0) < job.max_attempts:
        job.status = JobStatus.queued.value
        job.run_after = utcnow() + timedelta(seconds=delay_s)
    else:
        job.status, job.finished_at = JobStatus.failed.value, utcnow()


def request_pause(db: Session, job: GenerationJob) -> None:
    if job.status == JobStatus.running.value:
        job.status = JobStatus.cancel_requested.value
    elif job.status == JobStatus.queued.value:
        job.status = JobStatus.paused.value


def resume(db: Session, job: GenerationJob, extra_params: dict | None = None) -> None:
    if job.status in (JobStatus.paused.value, JobStatus.failed.value, JobStatus.awaiting_approval.value):
        if extra_params:
            job.params = {**(job.params or {}), **extra_params}
        job.status, job.error, job.run_after, job.finished_at = JobStatus.queued.value, "", None, None
        job.attempts = 0


def run_pending_jobs(max_jobs: int = 50) -> int:
    """Synchronously drain the queue (used by tests and the CLI worker)."""
    n = 0
    while n < max_jobs:
        db = SessionLocal()
        try:
            jid = claim_next(db)
        finally:
            db.close()
        if jid is None:
            break
        run_job(jid)
        n += 1
    return n


_loaded = False


def _ensure_handlers_loaded() -> None:
    global _loaded
    if not _loaded:
        from app.services import tasks  # noqa: F401  (registers handlers)

        _loaded = True


class Worker(threading.Thread):
    """Embedded worker thread (free-tier hosts often lack separate worker dynos)."""

    def __init__(self) -> None:
        super().__init__(daemon=True, name="researchlab-worker")
        self._stop = threading.Event()
        self._last_watch_check = datetime.min.replace(tzinfo=timezone.utc)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        _ensure_handlers_loaded()
        s = get_settings()
        log.info("worker %s started", WORKER_ID)
        while not self._stop.is_set():
            try:
                self._maybe_schedule_watches(s.watch_check_minutes)
                db = SessionLocal()
                try:
                    jid = claim_next(db)
                finally:
                    db.close()
                if jid:
                    run_job(jid)
                    continue
            except Exception as e:  # noqa: BLE001
                log.error("worker loop error: %s", e)
            self._stop.wait(s.worker_poll_seconds)

    def _maybe_schedule_watches(self, minutes: int) -> None:
        if utcnow() - self._last_watch_check < timedelta(minutes=minutes):
            return
        self._last_watch_check = utcnow()
        from app.services.tasks import schedule_due_watches

        schedule_due_watches()
