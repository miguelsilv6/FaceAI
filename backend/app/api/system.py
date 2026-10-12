from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..config import DETECTOR_BACKENDS, RECOGNITION_MODELS, settings
from ..schemas import JobParams
from ..services.face_engine import default_threshold, get_engine

router = APIRouter(prefix="/api", tags=["sistema"])


@router.get("/system")
def system_info() -> dict:
    return {
        "version": __version__,
        "environment": get_engine().environment(),
        "models": [{"name": m, "default_threshold": default_threshold(m)} for m in RECOGNITION_MODELS],
        "detectors": DETECTOR_BACKENDS,
        "defaults": JobParams(model=settings.default_model, detector=settings.default_detector).model_dump(),
    }
