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
        pending = s.exec(select(Video).where((Video.sha256 == "") | (Video.quick_hash == ""))).all()
        for v in pending:
            try:
                if not Path(v.stored_path).exists():
                    continue
                v.sha256 = v.sha256 or file_sha256(v.stored_path)
                v.quick_hash = v.quick_hash or quick_hash(v.stored_path)
            except OSError:
                log.exception("Não foi possível calcular o hash do vídeo %s", v.id)
                continue
            s.add(v)
            s.commit()
            done += 1
    return done


def start_backfill() -> None:
    threading.Thread(target=backfill_video_hashes, name="video-hash-backfill", daemon=True).start()
