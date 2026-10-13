"""Interface comum dos motores faciais e seleção do motor ativo.

Há dois motores:
- ``onnx`` (por omissão): SCRFD + ArcFace (InsightFace) via ONNX Runtime — rápido em CPU, sem TensorFlow.
- ``deepface``: DeepFace/TensorFlow (RetinaFace, ArcFace, Facenet512, ...), opcional.

O motor é acedido por `get_engine()`, o que permite substituí-lo nos testes por um motor falso.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from ..config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceObservation:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    embedding: np.ndarray
    # 5 pontos (olho esq., olho dir., nariz, canto esq. e dir. da boca) em coordenadas da imagem, se o detetor os der.
    landmarks: np.ndarray | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ModelInfo:
    name: str
    default_threshold: float  # distância de cosseno máxima recomendada
    description: str = ""


class FaceEngine(Protocol):
    name: str

    def detect(
        self, image_bgr: np.ndarray, *, model: str, detector: str, upscale: float = 1.0
    ) -> list[FaceObservation]:
        """Deteta todas as faces e calcula o embedding de cada uma."""

    def count_faces(self, image_bgr: np.ndarray, *, detector: str) -> int:
        """Deteção rápida (sem embeddings) usada como primeira passagem da cascata."""

    def models(self) -> list[ModelInfo]: ...

    def detectors(self) -> list[str]: ...

    def default_fast_detector(self) -> str | None: ...

    def environment(self) -> dict[str, Any]: ...


def default_threshold(model: str, engine: FaceEngine | None = None) -> float:
    engine = engine or get_engine()
    for m in engine.models():
        if m.name == model:
            return m.default_threshold
    return 0.5


_engine: FaceEngine | None = None


def create_engine(name: str | None = None) -> FaceEngine:
    name = (name or settings.engine).lower()
    if name == "deepface":
        from .engine_deepface import DeepFaceEngine

        return DeepFaceEngine()
    if name == "onnx":
        from .engine_onnx import OnnxEngine

        return OnnxEngine()
    raise ValueError(f"Motor desconhecido: {name!r} (use 'onnx' ou 'deepface')")


def get_engine() -> FaceEngine:
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def set_engine(engine: FaceEngine | None) -> None:
    global _engine
    _engine = engine


def preload_models() -> None:
    """Carrega/descarrega os modelos do motor em segundo plano no arranque, para que o 1.º upload ou a 1.ª
    análise não fiquem à espera do download (que atrás de um proxy pode exceder o tempo limite do pedido)."""
    if os.environ.get("FACEAI_PRELOAD_MODELS", "1") != "1":
        return

    def _run() -> None:
        engine = get_engine()
        warmup = getattr(engine, "warmup", None)
        if warmup is None:
            return
        try:
            warmup()
            log.info("Modelos do motor '%s' prontos.", engine.name)
        except Exception as exc:  # sem rede, por exemplo: o erro volta a aparecer na 1.ª utilização
            log.warning("Não foi possível pré-carregar os modelos: %s", exc)

    threading.Thread(target=_run, daemon=True, name="faceai-preload").start()
