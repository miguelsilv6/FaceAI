"""Impressões digitais dos vídeos: SHA-256 completo (integridade e duplicados) e "quick hash" (pré-verificação).

quick_hash = SHA-256(tamanho em 8 bytes little-endian ‖ primeiro MiB ‖ último MiB). O browser calcula o mesmo
valor antes de enviar o ficheiro, para detetar um duplicado sem ter de o enviar.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

from sqlmodel import Session, select

from ..db import get_engine
from ..models import Video

log = logging.getLogger(__name__)

CHUNK = 4 * 1024 * 1024
QUICK_SPAN = 1024 * 1024


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def quick_hash(path: str | Path) -> str:
    path = Path(path)
    size = path.stat().st_size
    h = hashlib.sha256(size.to_bytes(8, "little"))
    with path.open("rb") as fh:
        h.update(fh.read(min(QUICK_SPAN, size)))
        fh.seek(max(0, size - QUICK_SPAN))
        h.update(fh.read(min(QUICK_SPAN, size)))
    return h.hexdigest()


def backfill_video_hashes() -> int:
    """Calcula os hashes dos vídeos importados por versões anteriores (para a deteção de duplicados)."""
    done = 0
    with Session(get_engine()) as s:
        pending = list(s.exec(select(Video.id, Video.stored_path, Video.sha256, Video.quick_hash)
                              .where((Video.sha256 == "") | (Video.quick_hash == ""))))
    for vid, path, sha, quick in pending:
        # O hash de um vídeo grande demora: calcula-se sem nenhuma sessão aberta e grava-se numa sessão curta.
        try:
            if not Path(path).exists():
                continue
            sha = sha or file_sha256(path)
            quick = quick or quick_hash(path)
        except OSError:
            log.exception("Não foi possível calcular o hash do vídeo %s", vid)
            continue
        with Session(get_engine()) as s:
            v = s.get(Video, vid)
            if v is None:
                continue
            v.sha256, v.quick_hash = v.sha256 or sha, v.quick_hash or quick
            s.add(v)
            s.commit()
        done += 1
    return done


def start_backfill() -> None:
    threading.Thread(target=backfill_video_hashes, name="video-hash-backfill", daemon=True).start()
