"""Log de auditoria append-only: cada ação fica na base de dados e numa linha JSON em audit.jsonl."""

from __future__ import annotations

import json
import threading
from contextvars import ContextVar
from typing import Any

from sqlmodel import Session

from ..config import settings
from ..models import AuditEvent

_file_lock = threading.Lock()
# IP do cliente do pedido HTTP em curso (definido por um middleware em main.py); vazio em tarefas de fundo.
request_ip: ContextVar[str] = ContextVar("request_ip", default="")


def log_event(
    session: Session,
    action: str,
    *,
    case_id: int | None = None,
    actor: str = "",
    details: dict[str, Any] | None = None,
    commit: bool = True,
    ip: str | None = None,
) -> AuditEvent:
    event = AuditEvent(case_id=case_id, actor=actor or "desconhecido", action=action, details=details or {},
                       ip=request_ip.get() if ip is None else ip)
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
        "ip": event.ip,
        "details": event.details,
    }
    settings.ensure_dirs()
    with _file_lock, settings.audit_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
