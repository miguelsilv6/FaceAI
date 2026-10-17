"""Motor DeepFace (TensorFlow) — opcional. Instalar com `pip install -e "backend[deepface]"`."""

from __future__ import annotations

import os
import platform
import threading
from functools import lru_cache
from typing import Any

import cv2
import numpy as np

from .face_engine import FaceObservation, ModelInfo

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
# O DeepFace (RetinaFace, ArcFace, ...) precisa do tf-keras: tem de ser definido antes do primeiro import do TensorFlow.
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")

MODELS = ["ArcFace", "Facenet512", "Facenet", "VGG-Face", "GhostFaceNet", "SFace"]
DETECTORS = ["retinaface", "mtcnn", "yunet", "centerface", "yolov8n", "opencv", "ssd"]
_LANDMARK_KEYS = ("left_eye", "right_eye", "nose", "mouth_left", "mouth_right")


class DeepFaceEngine:
    """Implementação com DeepFace. Um lock serializa as inferências."""

    name = "deepface"

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def detect(
        self, image_bgr: np.ndarray, *, model: str, detector: str, upscale: float = 1.0,
        min_confidence: float | None = None,  # o DeepFace não expõe o limiar do detetor; filtrado depois
        prefilter=None,  # o DeepFace calcula deteção e embedding juntos: o filtro é aplicado depois, em jobs.py
    ) -> list[FaceObservation]:
        from deepface import DeepFace

        img = image_bgr
        if upscale and upscale > 1.0:
            img = cv2.resize(img, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        height, width = img.shape[:2]

        with self._lock:
            results = DeepFace.represent(
                img_path=img, model_name=model, detector_backend=detector, enforce_detection=False, align=True
            )

        faces: list[FaceObservation] = []
        scale = upscale if upscale and upscale > 1.0 else 1.0
        for r in results:
            area = r.get("facial_area") or {}
            x, y, w, h = (int(area.get(k, 0)) for k in ("x", "y", "w", "h"))
            confidence = float(r.get("face_confidence") or 0.0)
            if w <= 0 or h <= 0 or _is_no_detection(x, y, w, h, confidence, width, height):
                continue
            faces.append(
                FaceObservation(
                    x=int(round(x / scale)),
                    y=int(round(y / scale)),
                    w=int(round(w / scale)),
                    h=int(round(h / scale)),
                    confidence=confidence,
                    embedding=np.asarray(r["embedding"], dtype=np.float32),
                    landmarks=_landmarks(area, scale),
                )
            )
        return faces

    def count_faces(self, image_bgr: np.ndarray, *, detector: str, upscale: float = 1.0) -> int:
        from deepface import DeepFace

        if upscale and upscale > 1.0:
            image_bgr = cv2.resize(image_bgr, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        with self._lock:
            faces = DeepFace.extract_faces(img_path=image_bgr, detector_backend=detector, enforce_detection=False,
                                           align=False)
        h, w = image_bgr.shape[:2]
        return sum(
            1 for f in faces
            if not _is_no_detection(f["facial_area"]["x"], f["facial_area"]["y"], f["facial_area"]["w"],
                                    f["facial_area"]["h"], float(f.get("confidence") or 0.0), w, h)
        )

    def models(self) -> list[ModelInfo]:
        return [ModelInfo(m, _threshold(m)) for m in MODELS]

    def detectors(self) -> list[str]:
        return DETECTORS

    def default_fast_detector(self) -> str | None:
        return "opencv"

    def environment(self) -> dict[str, Any]:
        return _environment()


def _landmarks(area: dict, scale: float) -> np.ndarray | None:
    pts = [area.get(k) for k in _LANDMARK_KEYS]
    if any(p is None for p in pts[:2]):
        return None
    if any(p is None for p in pts):  # só os olhos
        pts = pts[:2]
    return np.asarray(pts, dtype=np.float32) / scale


def _is_no_detection(x: int, y: int, w: int, h: int, confidence: float, width: int, height: int) -> bool:
    """Sem faces, o DeepFace (enforce_detection=False) devolve a imagem inteira (às vezes com 1 px a menos)
    e confiança 0. Isso não é uma face."""
    return confidence <= 0.0 and x <= 1 and y <= 1 and w >= width - 2 and h >= height - 2


def _threshold(model: str) -> float:
    try:
        from deepface.modules.verification import find_threshold

        return float(find_threshold(model, "cosine"))
    except Exception:  # pragma: no cover
        return 0.4


@lru_cache(maxsize=1)
def _environment() -> dict[str, Any]:
    env: dict[str, Any] = {
        "engine": "deepface",
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
