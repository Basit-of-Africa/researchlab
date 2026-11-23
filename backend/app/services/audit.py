from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog

log = logging.getLogger("researchlab.audit")


def audit(
    db: Session,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str = "",
    entity_id: Any = "",
    project_id: uuid.UUID | None = None,
    details: dict | None = None,
    ip: str = "",
) -> None:
    """Append an audit record. Caller commits."""
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else "",
            project_id=project_id,
            details=details or {},
            ip=ip,
        )
    )
    log.info("audit action=%s entity=%s:%s", action, entity_type, entity_id)
