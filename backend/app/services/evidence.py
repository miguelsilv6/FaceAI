"""Verificação da integridade dos vídeos originais: o SHA-256 é recalculado e comparado com o da importação.

Uma alteração (adulteração, corrupção do disco) ou um ficheiro em falta fica registado no vídeo (`verified_ok`),
na auditoria (`evidence.verified`) e no relatório. A verificação de um processo corre em segundo plano (vídeos de
vários GB); o relatório verifica na hora os vídeos pequenos que não tenham verificação recente.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from ..db import get_engine
from ..models import Video, utcnow
from .audit import log_event
from .hashing import file_sha256

log = logging.getLogger(__name__)

RECENT = timedelta(hours=24)
INLINE_MAX_BYTES = 4 * 1024**3  # o relatório verifica na hora até 4 GiB de vídeos por verificar

_lock = threading.Lock()
_progress: dict[int, dict[str, Any]] = {}  # case_id → {running, done, total, current, started_at}


def verify_video(video: Video) -> tuple[bool, str]:
    """(ok, motivo). Não grava: quem chama atualiza o vídeo e a auditoria."""
    path = Path(video.stored_path)
    if video.original_removed_at is not None:  # convertido: o servidor guarda só a cópia sem perdas
        work = Path(video.analysis_path or "")
        if not video.analysis_path or not work.exists():
            return False, "original removido do servidor e cópia de análise em falta"
        try:
            ok = file_sha256(work) == video.analysis_sha256
        except OSError as exc:
            return False, f"erro de leitura da cópia de análise: {exc}"
        when = video.original_removed_at.strftime("%Y-%m-%d %H:%M")
        if not ok:
            return False, "original removido do servidor; a cópia de análise tem SHA-256 diferente"
        return True, (f"original removido do servidor em {when} UTC (SHA-256 {video.sha256}, preservado no suporte "
                      "de origem); cópia de análise íntegra")
    if not path.exists():
        return False, "ficheiro original em falta"
    if not video.sha256:
        return False, "sem SHA-256 de importação"
    try:
        digest = file_sha256(path)
    except OSError as exc:
        return False, f"erro de leitura: {exc}"
    if digest != video.sha256:
        return False, f"SHA-256 diferente ({digest})"
    if video.analysis_path and video.analysis_sha256:  # cópia de trabalho extraída (.sec): é a que foi analisada
        work = Path(video.analysis_path)
        if not work.exists():
            return False, "original íntegro, mas a cópia de análise extraída está em falta"
        try:
            work_digest = file_sha256(work)
        except OSError as exc:
            return False, f"erro de leitura da cópia de análise: {exc}"
        if work_digest != video.analysis_sha256:
            return False, f"original íntegro, mas a cópia de análise extraída tem SHA-256 diferente ({work_digest})"
    return True, ""


def verify_videos(s: Session, videos: list[Video], *, case_id: int, actor: str,
                  on_progress=None) -> dict[str, Any]:
    results = []
    for i, v in enumerate(videos):
        if on_progress:
            on_progress(i, v)
        ok, reason = verify_video(v)
        v.verified_at, v.verified_ok = utcnow(), ok
        s.add(v)
        s.commit()
        results.append({"video_id": v.id, "filename": v.filename, "ok": ok, "reason": reason,
                        "sha256": v.sha256})
    summary = {"videos": len(results), "ok": sum(r["ok"] for r in results),
               "failed": [r for r in results if not r["ok"]]}
    log_event(s, "evidence.verified", case_id=case_id, actor=actor, details=summary)
    return summary


def ensure_recent(s: Session, videos: list[Video], *, case_id: int, actor: str) -> None:
    """Antes de um relatório: verifica os vídeos sem verificação nas últimas 24 h, se forem pequenos."""
    if progress(case_id).get("running"):  # a verificação completa já está a correr
        return
    stale = [v for v in videos if v.verified_at is None or _naive(utcnow()) - _naive(v.verified_at) > RECENT]
    if stale and sum(v.size_bytes or 0 for v in stale) <= INLINE_MAX_BYTES:
        verify_videos(s, stale, case_id=case_id, actor=actor)


def _naive(dt):
    return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo else dt


def progress(case_id: int) -> dict[str, Any]:
    with _lock:
        return dict(_progress.get(case_id) or {"running": False})


def start_case_verification(case_id: int, actor: str) -> dict[str, Any]:
    with _lock:
        state = _progress.get(case_id)
        if state and state.get("running"):
            return dict(state)
        _progress[case_id] = {"running": True, "done": 0, "total": 0, "current": "",
                              "started_at": utcnow().isoformat()}
    threading.Thread(target=_run, args=(case_id, actor), name=f"evidence-{case_id}", daemon=True).start()
    return progress(case_id)


def _run(case_id: int, actor: str) -> None:
    try:
        with Session(get_engine(), expire_on_commit=False) as s:
            videos = list(s.exec(select(Video).where(Video.case_id == case_id).order_by(Video.id)))
            with _lock:
                _progress[case_id]["total"] = len(videos)

            def step(i: int, v: Video) -> None:
                with _lock:
                    _progress[case_id].update(done=i, current=v.filename)

            summary = verify_videos(s, videos, case_id=case_id, actor=actor, on_progress=step)
        with _lock:
            _progress[case_id] = {"running": False, "done": summary["videos"], "total": summary["videos"],
                                  "ok": summary["ok"], "failed": summary["failed"],
                                  "finished_at": utcnow().isoformat()}
    except Exception as exc:  # pragma: no cover - registado e mostrado na interface
        log.exception("Verificação de integridade do processo %s falhou", case_id)
        with _lock:
            _progress[case_id] = {"running": False, "error": str(exc)}
