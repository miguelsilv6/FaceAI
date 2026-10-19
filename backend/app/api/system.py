from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import __version__
from ..schemas import JobParams
from ..services import storage
from ..services.body_engine import DEFAULT_PERSON_DETECTOR, DEFAULT_REID_THRESHOLD, PERSON_DETECTORS
from ..services.face_engine import get_engine
from .deps import AdminUser, SessionDep, get_current_user
from .uploads import chunk_size

router = APIRouter(prefix="/api", tags=["sistema"], dependencies=[Depends(get_current_user)])


def engine_defaults() -> JobParams:
    engine = get_engine()
    model = engine.models()[0]
    return JobParams(model=model.name, detector=engine.detectors()[0], threshold=model.default_threshold,
                     fast_detector=engine.default_fast_detector(), person_detector=DEFAULT_PERSON_DETECTOR,
                     reid_threshold=DEFAULT_REID_THRESHOLD)


@router.get("/system")
def system_info() -> dict:
    engine = get_engine()
    return {
        "version": __version__,
        "engine": engine.name,
        "environment": engine.environment(),
        "models": [{"name": m.name, "default_threshold": m.default_threshold, "description": m.description}
                   for m in engine.models()],
        "detectors": engine.detectors(),
        "person_detectors": list(PERSON_DETECTORS),
        "defaults": engine_defaults().model_dump(),
        "upload_chunk_bytes": chunk_size(),
    }


@router.get("/system/storage")
def system_storage(session: SessionDep, _: AdminUser) -> dict:
    """Armazenamento global (administrador): disco, categorias, base de dados e processos maiores."""
    return storage.global_storage(session)
