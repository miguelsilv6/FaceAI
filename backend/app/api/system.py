from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..schemas import JobParams
from ..services.face_engine import get_engine
from .uploads import chunk_size

router = APIRouter(prefix="/api", tags=["sistema"])


def engine_defaults() -> JobParams:
    engine = get_engine()
    model = engine.models()[0]
    return JobParams(model=model.name, detector=engine.detectors()[0], threshold=model.default_threshold,
                     fast_detector=engine.default_fast_detector())


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
        "defaults": engine_defaults().model_dump(),
        "upload_chunk_bytes": chunk_size(),
    }
