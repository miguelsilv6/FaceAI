"""Pasta de dados movida ou restaurada noutro sítio (FACEAI_DATA_DIR diferente, outro disco, backup restaurado).

Os caminhos dos ficheiros são guardados por extenso na base de dados. No arranque, se esses caminhos apontam para
uma pasta antiga que já não existe e os mesmos ficheiros existem na pasta de dados atual, os caminhos são
atualizados (uma vez, numa transação, com registo na auditoria). Nada é feito se os ficheiros não forem
encontrados na pasta atual: os caminhos antigos ficam como estavam.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine
from ..models import Video
from .audit import log_event

log = logging.getLogger(__name__)

# (tabela, coluna) com caminhos de ficheiros dentro da pasta de dados
PATH_COLUMNS = [
    ("video", "stored_path"), ("video", "playback_path"),
    ("referenceimage", "stored_path"), ("referenceimage", "face_crop_path"),
    ("detection", "crop_path"), ("detection", "frame_path"),
    ("savedframe", "stored_path"), ("upload", "temp_path"),
]
MARKERS = ("/cases/", "/uploads/")


def _old_root(path: str) -> str | None:
    norm = path.replace("\\", "/")
    for marker in MARKERS:
        i = norm.find(marker)
        if i > 0:
            return path[:i]
    return None


def relocate_data_dir() -> int:
    """Atualiza os caminhos de uma pasta de dados antiga para a atual. Devolve o n.º de caminhos alterados."""
    current = str(settings.data_dir)
    with Session(get_engine()) as s:
        candidates: dict[str, str] = {}
        for path in s.exec(select(Video.stored_path).limit(500)):
            root = _old_root(path or "")
            if root and root != current and root not in candidates:
                candidates[root] = path
        moved = 0
        for root, sample in candidates.items():
            rel = sample[len(root):].lstrip("/\\")
            if Path(sample).exists() or not (settings.data_dir / rel).exists():
                continue  # a pasta antiga ainda existe, ou os ficheiros não estão na pasta atual
            for table, col in PATH_COLUMNS:
                r = s.exec(text(f'UPDATE "{table}" SET "{col}" = :new || substr("{col}", :n) '
                                f'WHERE substr("{col}", 1, :len) = :old'),
                           params={"new": current, "n": len(root) + 1, "len": len(root), "old": root})
                moved += r.rowcount or 0
            s.commit()
            log.warning("Pasta de dados movida: %s → %s (%d caminho(s) atualizado(s))", root, current, moved)
            log_event(s, "system.data_dir_relocated", actor="sistema",
                      details={"from": root, "to": current, "paths_updated": moved})
        return moved
