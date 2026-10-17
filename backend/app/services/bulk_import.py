"""Importação em bloco a partir de uma pasta do servidor (FACEAI_IMPORT_DIR): cada pasta é um local.

Estrutura esperada (a pasta escolhida pode ser a raiz ou qualquer subpasta):

    <pasta escolhida>/
        Café Central/      → local "Café Central" (todos os vídeos desta pasta e das suas subpastas)
            cam1.mp4
            cam2/entrada.avi
        Banco/             → local "Banco"
        solto.mp4          → local com o nome da pasta escolhida

Os ficheiros são **copiados** para o processo (os originais ficam intactos), com o SHA-256 calculado durante a
cópia. Os duplicados (já no processo ou repetidos na importação) são ignorados e listados. Tudo corre numa thread
de fundo; o progresso fica em memória e o resumo na auditoria (import.server_started / import.server_finished).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine
from ..models import Location, utcnow
from . import disk
from .audit import log_event
from .hashing import quick_hash

log = logging.getLogger(__name__)

MAX_FILES = 20_000
COPY_CHUNK = 8 * 1024 * 1024

_lock = threading.Lock()
_progress: dict[int, dict[str, Any]] = {}  # case_id → estado da importação


def root() -> Path:
    if settings.import_dir is None:
        raise HTTPException(404, "A importação de uma pasta do servidor não está configurada (FACEAI_IMPORT_DIR).")
    if not settings.import_dir.is_dir():
        raise HTTPException(503, f"A pasta de importação não está disponível: {settings.import_dir}")
    return settings.import_dir


def resolve(rel: str) -> Path:
    """Caminho relativo à pasta de importação → absoluto, recusando qualquer saída dela (.., symlinks)."""
    base = root()
    target = (base / (rel or "").lstrip("/\\")).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(403, "Caminho fora da pasta de importação.")
    return target


def rel_of(path: Path) -> str:
    return path.relative_to(root()).as_posix() if path != root() else ""


def _videos_in(folder: Path, recursive: bool, video_ext: set[str], budget: list[int]) -> list[Path]:
    out: list[Path] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if Path(name).suffix.lower() in video_ext and not name.startswith("."):
                    out.append(Path(dirpath) / name)
                    budget[0] -= 1
                    if budget[0] <= 0:
                        return out
    else:
        out = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")
                     and p.suffix.lower() in video_ext)
        budget[0] -= len(out)
    return out


def scan(s: Session, case_id: int, rel: str, video_ext: set[str]) -> dict[str, Any]:
    folder = resolve(rel)
    if not folder.is_dir():
        raise HTTPException(404, "Pasta não encontrada.")
    existing = {loc.name.casefold(): loc for loc in s.exec(select(Location).where(Location.case_id == case_id))}
    subdirs = sorted((p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")),
                     key=lambda p: p.name.casefold())
    budget = [MAX_FILES]
    groups = []
    for d in subdirs:
        files = _videos_in(d, True, video_ext, budget)
        groups.append((d, files))
    loose = _videos_in(folder, False, video_ext, budget)
    if loose:
        groups.insert(0, (folder, loose))
    plan = []
    for d, files in groups:
        if not files:
            continue
        name = " ".join((d.name if d != root() else "Pasta de importação").split())
        loc = existing.get(name.casefold())
        plan.append({
            "path": rel_of(d), "location_name": loc.name if loc else name,
            "existing_location_id": loc.id if loc else None,
            "videos": [{"name": f.name, "path": rel_of(f), "size": f.stat().st_size,
                        "subfolder": f.parent.relative_to(d).as_posix() if f.parent != d else ""} for f in files],
        })
    return {
        "enabled": True, "root": str(root()), "path": rel_of(folder),
        "parent": rel_of(folder.parent) if folder != root() else None,
        "dirs": [{"name": d.name, "path": rel_of(d)} for d in subdirs],
        "plan": plan, "total_bytes": sum(v["size"] for g in plan for v in g["videos"]),
        "truncated": budget[0] <= 0,
    }


def progress(case_id: int) -> dict[str, Any]:
    with _lock:
        return dict(_progress.get(case_id) or {"running": False})


def start(case_id: int, actor: str, folders: list[dict[str, Any]], video_ext: set[str]) -> dict[str, Any]:
    """folders: [{path, location_name}]. Valida tudo antes de começar; a cópia corre em segundo plano."""
    items: list[tuple[str, Path]] = []
    for f in folders:
        name = " ".join(str(f.get("location_name") or "").split())
        if not name:
            raise HTTPException(422, "Indique o nome do local de cada pasta.")
        folder = resolve(str(f.get("path") or ""))
        if not folder.is_dir():
            raise HTTPException(404, f"Pasta não encontrada: {f.get('path')}")
        # A pasta escolhida em si (vídeos soltos) não inclui as subpastas: essas são outros locais.
        for v in _videos_in(folder, bool(f.get("recursive", True)), video_ext, [MAX_FILES]):
            items.append((name, v))
    if not items:
        raise HTTPException(422, "Nenhum vídeo para importar nas pastas escolhidas.")
    total_bytes = sum(p.stat().st_size for _, p in items)
    disk.check_space(total_bytes)
    with _lock:
        if (_progress.get(case_id) or {}).get("running"):
            raise HTTPException(409, "Já está a decorrer uma importação neste processo.")
        _progress[case_id] = {"running": True, "done": 0, "total": len(items), "bytes_done": 0,
                              "bytes_total": total_bytes, "current": "", "imported": 0, "duplicates": [],
                              "errors": [], "locations_created": [], "started_at": utcnow().isoformat()}
    threading.Thread(target=_run, args=(case_id, actor, items), name=f"import-{case_id}", daemon=True).start()
    return progress(case_id)


def _copy_with_hash(src: Path, dest: Path, on_bytes) -> str:
    h = hashlib.sha256()
    part = dest.with_name(dest.name + ".part")
    with src.open("rb") as fin, part.open("wb") as fout:
        while chunk := fin.read(COPY_CHUNK):
            fout.write(chunk)
            h.update(chunk)
            on_bytes(len(chunk))
        fout.flush()
        os.fsync(fout.fileno())
    part.replace(dest)
    return h.hexdigest()


def _location(s: Session, case_id: int, name: str, actor: str, created: list[str]) -> Location:
    for loc in s.exec(select(Location).where(Location.case_id == case_id)):
        if loc.name.casefold() == name.casefold():
            return loc
    loc = Location(case_id=case_id, name=name, created_by=actor)
    s.add(loc)
    s.commit()
    s.refresh(loc)
    log_event(s, "location.created", case_id=case_id, actor=actor,
              details={"location_id": loc.id, "name": loc.name, "source": "importação em bloco (servidor)"})
    created.append(name)
    return loc


def _update(case_id: int, **kw) -> None:
    with _lock:
        _progress[case_id].update(kw)


def _run(case_id: int, actor: str, items: list[tuple[str, Path]]) -> None:
    from ..api.cases import duplicate_error, find_duplicate, register_video  # import local (ciclo api ↔ service)

    imported: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    created: list[str] = []
    bytes_done = 0
    try:
        with Session(get_engine(), expire_on_commit=False) as s:
            log_event(s, "import.server_started", case_id=case_id, actor=actor,
                      details={"files": len(items), "folders": sorted({n for n, _ in items}),
                               "root": str(settings.import_dir)})
            dest_dir = settings.case_dir(case_id) / "videos"
            dest_dir.mkdir(parents=True, exist_ok=True)
            for i, (loc_name, src) in enumerate(items):
                rel = rel_of(src)
                _update(case_id, done=i, current=rel)
                size = 0
                try:
                    size = src.stat().st_size
                    existing = find_duplicate(s, case_id, quick=quick_hash(src), size=size)
                    if existing is not None:  # já no processo (ou repetido nesta importação): não copia
                        duplicates.append({"path": rel, "message": duplicate_error(s, existing, src.name).detail})
                    else:
                        loc = _location(s, case_id, loc_name, actor, created)
                        dest = dest_dir / f"{uuid.uuid4().hex}{src.suffix.lower()}"
                        copied = [0]

                        def on_bytes(n: int, base=bytes_done, copied=copied) -> None:
                            copied[0] += n
                            _update(case_id, bytes_done=base + copied[0])

                        digest = _copy_with_hash(src, dest, on_bytes)
                        video = register_video(s, case_id, dest, src.name, size, actor, loc.id, sha256=digest)
                        imported.append({"path": rel, "video_id": video.id, "location": loc.name,
                                         "sha256": digest})
                except HTTPException as exc:  # 409 duplicado (SHA-256) · 422 vídeo ilegível
                    s.rollback()
                    (duplicates if exc.status_code == 409 else errors).append({"path": rel,
                                                                               "message": str(exc.detail)})
                except OSError as exc:
                    s.rollback()
                    errors.append({"path": rel, "message": f"{src.name}: {exc.strerror or exc}"})
                bytes_done += size
                _update(case_id, imported=len(imported), duplicates=list(duplicates), errors=list(errors),
                        locations_created=list(created), bytes_done=bytes_done)
            disk.forget(case_id)
            log_event(s, "import.server_finished", case_id=case_id, actor=actor,
                      details={"imported": imported, "duplicates": duplicates, "errors": errors,
                               "locations_created": created})
        _update(case_id, running=False, done=len(items), current="", finished_at=utcnow().isoformat(),
                imported=len(imported))
    except Exception as exc:  # pragma: no cover - registado e mostrado na interface
        log.exception("Importação em bloco do processo %s falhou", case_id)
        _update(case_id, running=False, error=str(exc), finished_at=utcnow().isoformat())
