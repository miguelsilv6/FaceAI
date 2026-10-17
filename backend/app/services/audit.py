"""Log de auditoria append-only: cada ação fica na base de dados e numa linha JSON em audit.jsonl.

Os eventos formam uma cadeia de hashes: ``hash = SHA-256(prev_hash ‖ evento canónico)``, pela ordem do id. Alterar,
apagar ou inserir um evento a meio quebra a cadeia a partir desse ponto (ver ``verify_chain``). O audit.jsonl
recebe também o hash de cada evento, o que permite detetar a remoção dos últimos eventos da base de dados.

A cadeia é selada logo a seguir ao commit, por ordem de id e sob um lock, por isso dois pedidos em simultâneo
nunca ramificam a cadeia. Eventos de uma transação que não chegou ao commit nunca são selados nem escritos no
audit.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from contextvars import ContextVar
from typing import Any

from sqlalchemy import event as sa_event
from sqlmodel import Session, select

from ..config import settings
from ..models import AuditEvent

log = logging.getLogger(__name__)

_file_lock = threading.Lock()
_seal_lock = threading.Lock()
GENESIS = "0" * 64
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
        seal_pending()
    elif not session.info.get("audit_seal_pending"):
        # Selado quando a transação do chamador fizer commit (nunca, se for revertida).
        session.info["audit_seal_pending"] = True

        def _after_commit(_session) -> None:
            _session.info.pop("audit_seal_pending", None)
            seal_pending()

        sa_event.listen(session, "after_commit", _after_commit, once=True)
    return event


def canonical(event: AuditEvent) -> str:
    return json.dumps({
        "id": event.id,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "case_id": event.case_id,
        "actor": event.actor,
        "ip": event.ip,
        "action": event.action,
        "details": event.details,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def event_hash(prev_hash: str, event: AuditEvent) -> str:
    return hashlib.sha256((prev_hash + canonical(event)).encode("utf-8")).hexdigest()


def seal_pending(*, write_jsonl: bool = True) -> int:
    """Calcula o hash dos eventos ainda por selar (pela ordem do id) e escreve-os no audit.jsonl."""
    from ..db import get_engine  # import local: db → models (sem ciclos no arranque)

    try:
        with _seal_lock, Session(get_engine(), expire_on_commit=False) as s:
            pending = list(s.exec(select(AuditEvent).where(AuditEvent.hash == "").order_by(AuditEvent.id)))
            if not pending:
                return 0
            last = s.exec(select(AuditEvent.hash).where(AuditEvent.hash != "", AuditEvent.id < pending[0].id)
                          .order_by(AuditEvent.id.desc()).limit(1)).first()
            prev = last or GENESIS
            for ev in pending:
                ev.prev_hash = prev
                ev.hash = prev = event_hash(prev, ev)
                s.add(ev)
            s.commit()
            if write_jsonl:
                for ev in pending:
                    _append_jsonl(ev)
            return len(pending)
    except Exception:  # pragma: no cover - a selagem é retomada no próximo evento ou no arranque
        log.exception("Auditoria: não foi possível selar os eventos pendentes")
        return 0


def seal_on_startup() -> None:
    """Sela os eventos que ficaram por selar (ex.: o servidor parou entre o commit e a selagem).

    Na primeira execução desta versão, os eventos antigos (já escritos no audit.jsonl, sem hash) são selados sem
    os voltar a escrever no ficheiro."""
    from ..db import get_engine

    with Session(get_engine()) as s:
        first_run = s.exec(select(AuditEvent.id).where(AuditEvent.hash != "").limit(1)).first() is None
    sealed = seal_pending(write_jsonl=not first_run)
    if sealed and first_run:
        log.info("Auditoria: %d evento(s) anteriores incluídos na cadeia de hashes", sealed)


def verify_chain(session: Session) -> dict[str, Any]:
    """Recalcula a cadeia inteira. Devolve {ok, events, unsealed, broken_at, reason, last_hash}."""
    prev = GENESIS
    count = unsealed = 0
    for ev in session.exec(select(AuditEvent).order_by(AuditEvent.id)):
        if not ev.hash:
            unsealed += 1
            continue
        count += 1
        if ev.prev_hash != prev:
            return {"ok": False, "events": count, "unsealed": unsealed, "broken_at": ev.id,
                    "reason": "evento anterior em falta ou alterado (prev_hash não coincide)", "last_hash": prev}
        expected = event_hash(prev, ev)
        if expected != ev.hash:
            return {"ok": False, "events": count, "unsealed": unsealed, "broken_at": ev.id,
                    "reason": "o conteúdo do evento foi alterado (hash não coincide)", "last_hash": prev}
        prev = ev.hash
    jsonl = _jsonl_last_hash()
    result: dict[str, Any] = {"ok": True, "events": count, "unsealed": unsealed, "broken_at": None, "reason": "",
                              "last_hash": prev, "jsonl_last_hash": jsonl}
    if jsonl and jsonl != prev and count:
        result.update(ok=False, reason="o último evento do audit.jsonl não está na base de dados "
                                       "(eventos removidos do fim da cadeia?)")
    return result


def _jsonl_last_hash() -> str | None:
    path = settings.audit_log_path
    if not path.exists():
        return None
    with _file_lock, path.open("rb") as fh:  # só o fim do ficheiro (pode ter centenas de MB)
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 512 * 1024))
        lines = fh.read().splitlines()
    for line in reversed(lines):
        if b'"hash"' not in line:
            continue
        try:
            value = json.loads(line).get("hash")
        except ValueError:
            continue
        if value:
            return value
    return None


def _append_jsonl(event: AuditEvent) -> None:
    record = {
        "id": event.id,
        "timestamp": event.timestamp.isoformat(),
        "case_id": event.case_id,
        "actor": event.actor,
        "action": event.action,
        "ip": event.ip,
        "details": event.details,
        "prev_hash": event.prev_hash,
        "hash": event.hash,
    }
    settings.ensure_dirs()
    with _file_lock, settings.audit_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
