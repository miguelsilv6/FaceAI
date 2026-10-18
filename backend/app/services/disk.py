"""Espaço ocupado em disco por caso (para a lista de casos).

Conta a pasta do caso (vídeos, cópias de reprodução, referências, análises: recortes, frames e PNG originais em
cache) e os ficheiros do caso guardados fora dela (uploads por concluir em data/uploads). O resultado fica em cache
alguns segundos para a lista não percorrer o disco a cada pedido.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path

from sqlmodel import Session, select

from ..config import settings
from ..models import ReferenceImage, Upload, Video

log = logging.getLogger(__name__)

CACHE_SECONDS = 60
_cache: dict[int, tuple[float, int]] = {}
_lock = threading.Lock()


def dir_size(path: Path) -> int:
    """Soma dos tamanhos dos ficheiros (recursivo, sem seguir symlinks)."""
    total = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _outside_files(s: Session, case_id: int, folder: Path) -> int:
    paths = [u.temp_path for u in s.exec(select(Upload).where(Upload.case_id == case_id))]
    for v in s.exec(select(Video).where(Video.case_id == case_id)):
        paths += [v.stored_path, v.analysis_path or "", v.playback_path or ""]
    for r in s.exec(select(ReferenceImage).where(ReferenceImage.case_id == case_id)):
        paths += [r.stored_path, r.face_crop_path or ""]
    total = 0
    root = folder.resolve()
    for p in {p for p in paths if p}:
        path = Path(p)
        try:
            if root not in path.resolve().parents and path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def case_size(s: Session, case_id: int, *, fresh: bool = False) -> int:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(case_id)
    if hit and not fresh and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    folder = settings.case_dir(case_id)
    size = dir_size(folder) + _outside_files(s, case_id, folder)
    with _lock:
        _cache[case_id] = (now, size)
    return size


def forget(case_id: int) -> None:
    with _lock:
        _cache.pop(case_id, None)


# ----------------------------------------------------------------------------- alertas de espaço livre
LEVELS = ("ok", "warning", "critical")
_last_alert = {"level": "ok"}


def disk_status() -> dict:
    """Ocupação do disco da pasta de dados e nível de alerta (FACEAI_DISK_WARN/CRITICAL_PERCENT,
    FACEAI_DISK_MIN_FREE_GB)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(settings.data_dir)
    percent = round(100.0 * usage.used / usage.total, 1) if usage.total else 0.0
    min_free = settings.disk_min_free_gb * 1024**3
    if percent >= settings.disk_critical_percent or usage.free < min_free:
        level = "critical"
    elif percent >= settings.disk_warn_percent:
        level = "warning"
    else:
        level = "ok"
    return {"total": usage.total, "used": usage.used, "free": usage.free, "percent": percent, "level": level,
            "warn_percent": settings.disk_warn_percent, "critical_percent": settings.disk_critical_percent,
            "min_free_bytes": int(min_free)}


def check_space(needed_bytes: int) -> None:
    """Recusa (507) uma operação que deixaria o disco abaixo do espaço livre mínimo."""
    from fastapi import HTTPException

    st = disk_status()
    if st["free"] - max(0, needed_bytes) < st["min_free_bytes"]:
        raise HTTPException(507, f"Espaço em disco insuficiente no servidor: livres {st['free'] / 1024**3:.1f} GB, "
                                 f"são precisos {needed_bytes / 1024**3:.1f} GB + {st['min_free_bytes'] / 1024**3:.0f}"
                                 " GB de reserva. Liberte espaço (elimine análises ou processos antigos).")


def note_alert(s: Session, st: dict) -> None:
    """Regista na auditoria do sistema quando o nível de alerta sobe (uma vez por mudança)."""
    level = st["level"]
    previous = _last_alert["level"]
    _last_alert["level"] = level
    if LEVELS.index(level) > LEVELS.index(previous):
        from .audit import log_event

        log_event(s, "system.disk_alert", actor="sistema",
                  details={"level": level, "percent": st["percent"], "free_bytes": st["free"]})


def report_orphan_folders(s: Session) -> list[str]:
    """Pastas de processos/análises sem registo na base de dados (ex.: uma eliminação que não conseguiu apagar os
    ficheiros). Não são apagadas automaticamente (podem ser evidência); ficam no log para o administrador. Como os
    ids nunca são reutilizados, uma pasta destas nunca passa para um processo ou análise novo."""
    from ..models import Case, Job

    root = settings.data_dir / "cases"
    if not root.exists():
        return []
    cases = set(s.exec(select(Case.id)).all())
    jobs = set(s.exec(select(Job.id)).all())
    orphans = []
    for case_dir in root.glob("case_*"):
        try:
            cid = int(case_dir.name.split("_", 1)[1])
        except ValueError:
            continue
        if cid not in cases:
            orphans.append(str(case_dir))
            continue
        for jd in (case_dir / "jobs").glob("job_*"):
            try:
                if int(jd.name.split("_", 1)[1]) not in jobs:
                    orphans.append(str(jd))
            except ValueError:
                continue
    for path in orphans:
        log.warning("Pasta sem registo na base de dados (eliminação incompleta?): %s", path)
    return orphans
