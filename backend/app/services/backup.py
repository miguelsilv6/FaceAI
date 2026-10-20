"""Cópia de segurança consistente com o servidor a correr.

Copiar o ficheiro faceai.db diretamente, com o servidor ligado, pode dar uma cópia incompleta (as últimas escritas
ainda estão no ficheiro -wal). ``VACUUM INTO`` escreve um instantâneo consistente da base de dados. Com
``include_files``, copia também os ficheiros dos processos (vídeos, recortes, frames) e o audit.jsonl, de forma
incremental (só os ficheiros novos ou com outro tamanho). O manifesto lista o SHA-256 de cada vídeo original.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from .. import __version__
from ..config import settings
from ..db import get_engine
from ..models import SavedFrame, Video


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return str(Path(path).resolve().relative_to(settings.data_dir.resolve()))
    except ValueError:
        return path


def _copy_tree(src: Path, dst: Path) -> tuple[int, int]:
    copied = skipped = 0
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        target = dst / f.relative_to(src)
        if target.exists() and target.stat().st_size == f.stat().st_size:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        copied += 1
    return copied, skipped


def backup(dest: Path, *, include_files: bool = False) -> dict[str, Any]:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    db_out = dest / f"faceai_{stamp}.db"
    with get_engine().connect() as conn:
        conn.exec_driver_sql("VACUUM INTO '" + str(db_out).replace("'", "''") + "'")
    manifest: dict[str, Any] = {"created_at": stamp, "version": __version__, "data_dir": str(settings.data_dir),
                                "database": {"file": db_out.name, "sha256": _sha256(db_out)}}
    with Session(get_engine()) as s:
        manifest["videos"] = [{"id": v.id, "case_id": v.case_id, "filename": v.filename, "sha256": v.sha256,
                               "size_bytes": v.size_bytes, "path": _rel(v.stored_path),
                               **({"analysis_path": _rel(v.analysis_path), "analysis_sha256": v.analysis_sha256}
                                  if v.analysis_path else {}),
                               **({"original_removed_at": v.original_removed_at.isoformat()}
                                  if v.original_removed_at else {})}
                              for v in s.exec(select(Video).order_by(Video.id))]
        manifest["saved_frames"] = [{"id": f.id, "case_id": f.case_id, "sha256": f.sha256,
                                     "path": _rel(f.stored_path)}
                                    for f in s.exec(select(SavedFrame).order_by(SavedFrame.id))]
    if include_files:
        files = dest / "files"
        files.mkdir(exist_ok=True)
        copied, skipped = _copy_tree(settings.data_dir / "cases", files / "cases")
        if settings.audit_log_path.exists():
            shutil.copy2(settings.audit_log_path, files / "audit.jsonl")
        manifest["files"] = {"folder": "files", "copied": copied, "unchanged": skipped}
    # Sem a chave, os segredos MFA da cópia não se decifram (os utilizadores teriam de repor o MFA).
    key = settings.data_dir / "secret.key"
    if not settings.secret_key and key.exists():
        key_out = dest / "secret.key"
        shutil.copy2(key, key_out)
        key_out.chmod(0o600)
        manifest["secret_key"] = {"file": key_out.name, "note": "chave dos segredos MFA: guardar em local seguro"}
    elif settings.secret_key:
        manifest["secret_key"] = {"note": "definida em FACEAI_SECRET_KEY: guarde essa variável à parte"}
    (dest / f"manifest_{stamp}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")
    return manifest
