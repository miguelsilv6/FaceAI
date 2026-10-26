"""Armazenamento por processo e global: o que ocupa espaço, o que é evidência e o que é regenerável.

Categorias (pastas do processo):
- vídeos analisados (originais e cópias de análise sem perdas) e fotos de referência: evidência, não se apagam aqui;
- recortes das faces: prova e revisão, não se apagam aqui;
- cópias de reprodução: regeneráveis a partir do vídeo (ffmpeg), pedidas de novo ao abrir o leitor;
- frames anotados gravados por análises anteriores: regeneráveis (o frame vem do vídeo e a caixa está na BD);
- caches (frames limpos por n.º de frame, PNG originais, miniaturas): regeneráveis.

Libertar só apaga categorias regeneráveis, fica na auditoria (storage.freed) e nunca toca na evidência.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, select

from ..config import settings
from ..models import Case, Detection, Job, Video
from . import disk
from .audit import log_event

CATEGORIES: dict[str, tuple[str, bool]] = {  # chave → (descrição, regenerável)
    "originals": ("Vídeos originais", False),
    "analysis": ("Cópias de análise sem perdas", False),
    "references": ("Fotos de referência", False),
    "crops": ("Recortes das faces e corpos", False),
    "playback": ("Cópias de reprodução", True),
    "frames": ("Frames anotados (análises anteriores)", True),
    "caches": ("Caches (frames, PNG originais, miniaturas)", True),
    "other": ("Outros (embeddings, temporários)", False),
}
FREEABLE = ("playback", "frames", "caches")
CACHE_DIRS = ("frames_cache", "originals", "thumbs")
CACHE_SECONDS = 60
_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def _classify(rel: Path) -> str:
    parts = rel.parts
    if parts[0] == "videos":
        name = rel.name
        if "_playback" in name:
            return "playback"
        return "analysis" if "_analysis" in name else "originals"
    if parts[0] == "references":
        return "references"
    if parts[0] == "jobs" and len(parts) >= 4:
        folder = parts[2]
        if folder in ("crops", "body_crops"):
            return "crops"
        if folder == "frames":
            return "frames"
        if folder in CACHE_DIRS:
            return "caches"
        if folder == "body_frames":
            return "crops"  # amostras de corpo guardadas pela análise (poucas): ficam com os recortes
    return "other"


def case_storage(case_id: int, *, fresh: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(case_id)
    if hit and not fresh and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    root = settings.case_dir(case_id)
    sizes = {k: [0, 0] for k in CATEGORIES}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            bucket = sizes[_classify(path.relative_to(root))]
            bucket[0] += size
            bucket[1] += 1
    cats = [{"key": k, "label": label, "regenerable": regen, "bytes": sizes[k][0], "files": sizes[k][1]}
            for k, (label, regen) in CATEGORIES.items()]
    out = {"case_id": case_id, "total_bytes": sum(c["bytes"] for c in cats),
           "regenerable_bytes": sum(c["bytes"] for c in cats if c["regenerable"]), "categories": cats}
    with _lock:
        _cache[case_id] = (now, out)
    return out


def forget(case_id: int) -> None:
    with _lock:
        _cache.pop(case_id, None)
    disk.forget(case_id)


def global_storage(s: Session) -> dict[str, Any]:
    cases = []
    totals = {k: 0 for k in CATEGORIES}
    for case in s.exec(select(Case).order_by(Case.id)):
        info = case_storage(case.id)
        for c in info["categories"]:
            totals[c["key"]] += c["bytes"]
        cases.append({"case_id": case.id, "name": case.name, "total_bytes": info["total_bytes"],
                      "regenerable_bytes": info["regenerable_bytes"]})
    cases.sort(key=lambda c: -c["total_bytes"])
    db = settings.data_dir / "faceai.db"
    system = {"database": sum(p.stat().st_size for p in db.parent.glob(db.name + "*") if p.is_file()),
              "audit_log": settings.audit_log_path.stat().st_size if settings.audit_log_path.exists() else 0,
              "uploads_in_progress": disk.dir_size(settings.data_dir / "uploads")}
    return {"disk": disk.disk_status(), "categories": [
        {"key": k, "label": label, "regenerable": regen, "bytes": totals[k]}
        for k, (label, regen) in CATEGORIES.items()], "system": system, "cases": cases}


def free(s: Session, case_id: int, targets: list[str], *, actor: str) -> dict[str, Any]:
    """Apaga categorias regeneráveis do processo. Devolve {categoria: {files, bytes}}."""
    from .transcode import cancel_copies

    bad = [t for t in targets if t not in FREEABLE]
    if bad:
        raise ValueError(f"categoria não pode ser libertada: {', '.join(bad)}")
    root = settings.case_dir(case_id)
    result: dict[str, dict[str, int]] = {}

    def remove(paths) -> dict[str, int]:
        n = size = 0
        for p in paths:
            try:
                st = p.stat().st_size
                p.unlink()
            except OSError:
                continue
            n, size = n + 1, size + st
        return {"files": n, "bytes": size}

    if "playback" in targets:
        videos = list(s.exec(select(Video).where(Video.case_id == case_id, Video.playback_path.is_not(None))))
        cancel_copies([v.id for v in videos])
        result["playback"] = remove(Path(v.playback_path) for v in videos if v.playback_path)
        for v in videos:  # pedida de novo quando o vídeo for aberto no leitor
            v.playback_path, v.playback_status, v.playback_progress = None, "on_demand", 0.0
            v.playback_used_at = None
            s.add(v)
        s.commit()
    if "frames" in targets:
        job_ids = list(s.exec(select(Job.id).where(Job.case_id == case_id))) or [-1]
        result["frames"] = remove(p for p in root.glob("jobs/*/frames/*") if p.is_file())
        s.exec(update(Detection).where(Detection.job_id.in_(job_ids), Detection.frame_path.like("%/frames/%"))
               .values(frame_path=""))
        s.commit()
    if "caches" in targets:
        result["caches"] = remove(p for d in CACHE_DIRS for p in root.glob(f"jobs/*/{d}/*") if p.is_file())
        for d in CACHE_DIRS:
            for folder in root.glob(f"jobs/*/{d}"):
                shutil.rmtree(folder, ignore_errors=True)
    forget(case_id)
    log_event(s, "storage.freed", case_id=case_id, actor=actor,
              details={"targets": targets, "result": result,
                       "freed_bytes": sum(r["bytes"] for r in result.values()),
                       "note": "só ficheiros regeneráveis a partir do vídeo; a evidência não foi tocada"})
    return result
