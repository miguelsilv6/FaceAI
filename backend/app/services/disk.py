"""Espaço ocupado em disco por caso (para a lista de casos).

Conta a pasta do caso (vídeos, cópias de reprodução, referências, análises: recortes, frames e PNG originais em
cache) e os ficheiros do caso guardados fora dela (uploads por concluir em data/uploads). O resultado fica em cache
alguns segundos para a lista não percorrer o disco a cada pedido.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from sqlmodel import Session, select

from ..config import settings
from ..models import ReferenceImage, Upload, Video

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
        paths += [v.stored_path, v.playback_path or ""]
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
