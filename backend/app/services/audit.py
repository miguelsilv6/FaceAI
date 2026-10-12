"""Log de auditoria append-only: cada ação fica na base de dados e numa linha JSON em audit.jsonl."""

from __future__ import annotations

import json
import threading
from typing import Any

from sqlmodel import Session

from ..config import settings
from ..models import AuditEvent

_file_lock = threading.Lock()


def log_event(
    session: Session,
    action: str,
    *,
    case_id: int | None = None,
    actor: str = "",
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> AuditEvent:
    event = AuditEvent(case_id=case_id, actor=actor or "desconhecido", action=action, details=details or {})
    session.add(event)
    if commit:
        session.commit()
        session.refresh(event)
    _append_jsonl(event)
    return event


def _append_jsonl(event: AuditEvent) -> None:
    record = {
        "id": event.id,
        "timestamp": event.timestamp.isoformat(),
        "case_id": event.case_id,
        "actor": event.actor,
        "action": event.action,
        "details": event.details,
    }
    settings.ensure_dirs()
    with _file_lock, settings.audit_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
