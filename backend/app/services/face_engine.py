"""Camada sobre o DeepFace: deteção + embeddings, deteção de GPU/CPU e versões dos componentes.

O motor é acedido por `get_engine()`, o que permite substituí-lo nos testes por um motor falso.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import cv2
import numpy as np

log = logging.getLogger(__name__)

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
# O DeepFace (RetinaFace, ArcFace, ...) precisa do tf-keras: tem de ser definido antes do primeiro import do TensorFlow.
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")


@dataclass(frozen=True)
class FaceObservation:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    embedding: np.ndarray


class FaceEngine(Protocol):
    def detect(
        self, image_bgr: np.ndarray, *, model: str, detector: str, upscale: float = 1.0
    ) -> list[FaceObservation]: ...

    def environment(self) -> dict[str, Any]: ...


class DeepFaceEngine:
    """Implementação real com DeepFace (TensorFlow). Um lock serializa as inferências."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def detect(
        self, image_bgr: np.ndarray, *, model: str, detector: str, upscale: float = 1.0
    ) -> list[FaceObservation]:
        from deepface import DeepFace

        img = image_bgr
        if upscale and upscale > 1.0:
            img = cv2.resize(img, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        height, width = img.shape[:2]

        with self._lock:
            results = DeepFace.represent(
                img_path=img,
                model_name=model,
                detector_backend=detector,
                enforce_detection=False,
                align=True,
            )

        faces: list[FaceObservation] = []
        scale = upscale if upscale and upscale > 1.0 else 1.0
        for r in results:
            area = r.get("facial_area") or {}
            x, y, w, h = (int(area.get(k, 0)) for k in ("x", "y", "w", "h"))
            # Sem deteção, o DeepFace devolve a imagem inteira com confiança 0.
            if (x, y, w, h) == (0, 0, width, height) or w <= 0 or h <= 0:
                continue
            faces.append(
                FaceObservation(
                    x=int(round(x / scale)),
                    y=int(round(y / scale)),
                    w=int(round(w / scale)),
                    h=int(round(h / scale)),
                    confidence=float(r.get("face_confidence") or 0.0),
                    embedding=np.asarray(r["embedding"], dtype=np.float32),
                )
            )
        return faces

    def environment(self) -> dict[str, Any]:
        return _environment()


@lru_cache(maxsize=1)
def _environment() -> dict[str, Any]:
    env: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "opencv": cv2.__version__,
        "numpy": np.__version__,
    }
    try:
        import deepface

        env["deepface"] = getattr(deepface, "__version__", "?")
    except Exception as exc:  # pragma: no cover - depende da instalação
        env["deepface"] = f"indisponível ({exc})"
    try:
        import tensorflow as tf

        gpus = tf.config.list_physical_devices("GPU")
        env["tensorflow"] = tf.__version__
        env["device"] = "GPU" if gpus else "CPU"
        env["gpus"] = [g.name for g in gpus]
    except Exception as exc:  # pragma: no cover - depende da instalação
        env["tensorflow"] = f"indisponível ({exc})"
        env["device"] = "CPU"
        env["gpus"] = []
    return env


def default_threshold(model: str) -> float:
    """Limiar de distância de cosseno recomendado pelo DeepFace para o modelo."""
    try:
        from deepface.modules.verification import find_threshold

        return float(find_threshold(model, "cosine"))
    except Exception:  # pragma: no cover
        return 0.4


_engine: FaceEngine | None = None


def get_engine() -> FaceEngine:
    global _engine
    if _engine is None:
        _engine = DeepFaceEngine()
    return _engine


def set_engine(engine: FaceEngine | None) -> None:
    global _engine
    _engine = engine
