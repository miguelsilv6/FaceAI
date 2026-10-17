"""Importação em bloco a partir de uma pasta do servidor (FACEAI_IMPORT_DIR). Ver services/bulk_import.py.

Só o responsável do processo ou um administrador pode ver e importar desta pasta (pode conter material de outros
processos). A importação pelo browser (escolher pastas no computador) usa o upload em blocos normal.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import settings
from ..services import bulk_import
from .cases import VIDEO_EXT
from .deps import CurrentUser, SessionDep, authorize_objects, can_manage_case, get_case_or_404

router = APIRouter(prefix="/api", tags=["importação"], dependencies=[Depends(authorize_objects)])


class ImportFolder(BaseModel):
    path: str = Field("", max_length=4096)
    location_name: str = Field(..., min_length=1, max_length=200)
    recursive: bool = True


class ServerImport(BaseModel):
    folders: list[ImportFolder] = Field(..., min_length=1, max_length=500)


def _require(session, user, case_id: int) -> None:
    get_case_or_404(session, case_id)
    if not can_manage_case(session, user, case_id):
        raise HTTPException(403, "Só o responsável do processo ou um administrador pode importar da pasta do "
                                 "servidor.")


@router.get("/cases/{case_id}/import/server")
def browse_server_folder(case_id: int, session: SessionDep, user: CurrentUser, path: str = "") -> dict:
    """Subpastas de ``path`` e o plano de importação (cada subpasta com vídeos → um local)."""
    get_case_or_404(session, case_id)
    if settings.import_dir is None:
        return {"enabled": False}
    _require(session, user, case_id)
    return bulk_import.scan(session, case_id, path, VIDEO_EXT)


@router.post("/cases/{case_id}/import/server")
def start_server_import(case_id: int, body: ServerImport, session: SessionDep, user: CurrentUser) -> dict:
    _require(session, user, case_id)
    return bulk_import.start(case_id, user.username, [f.model_dump() for f in body.folders], VIDEO_EXT)


@router.get("/cases/{case_id}/import/server/status")
def server_import_status(case_id: int, session: SessionDep) -> dict:
    get_case_or_404(session, case_id)
    return bulk_import.progress(case_id)
