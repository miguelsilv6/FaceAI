"""Score de qualidade da face (0–1): nitidez, tamanho e frontalidade.

Serve para ordenar/filtrar deteções e explicar porque um match é fraco: uma face pequena, desfocada ou de perfil
produz embeddings menos fiáveis.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MIN_FACE_PX = 16   # abaixo disto, tamanho = 0
GOOD_FACE_PX = 96  # a partir disto, tamanho = 1
SHARPNESS_SCALE = 150.0


@dataclass(frozen=True)
class Quality:
    score: float
    sharpness: float
    size: float
    frontal: float | None

    def as_dict(self) -> dict[str, float | None]:
        return {"quality": round(self.score, 3), "sharpness": round(self.sharpness, 3),
                "size_score": round(self.size, 3),
                "frontal": None if self.frontal is None else round(self.frontal, 3)}


def sharpness_score(face_bgr: np.ndarray) -> float:
    """Variância do Laplaciano numa versão normalizada a 112×112 (independente do tamanho da face)."""
    if face_bgr.size == 0:
        return 0.0
    gray = cv2.cvtColor(cv2.resize(face_bgr, (112, 112), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return float(1.0 - np.exp(-var / SHARPNESS_SCALE))


def size_score(w: int, h: int) -> float:
    return float(np.clip((min(w, h) - MIN_FACE_PX) / (GOOD_FACE_PX - MIN_FACE_PX), 0.0, 1.0))


def frontal_score(landmarks: np.ndarray | None) -> float | None:
    """Estimativa da rotação lateral (yaw) a partir da posição do nariz em relação aos olhos."""
    if landmarks is None or len(landmarks) < 3:
        return None
    left_eye, right_eye, nose = landmarks[0], landmarks[1], landmarks[2]
    iod = float(np.linalg.norm(right_eye - left_eye))
    if iod < 1e-3:
        return 0.0
    mid = (left_eye + right_eye) / 2.0
    offset = abs(float(nose[0] - mid[0])) / iod
    return float(np.clip(1.0 - offset / 0.5, 0.0, 1.0))


def face_quality(image_bgr: np.ndarray, x: int, y: int, w: int, h: int,
                 landmarks: np.ndarray | None = None) -> Quality:
    crop = image_bgr[max(0, y):y + h, max(0, x):x + w]
    sharp = sharpness_score(crop)
    size = size_score(w, h)
    frontal = frontal_score(landmarks)
    parts = [(sharp, 0.4), (size, 0.3)] + ([(frontal, 0.3)] if frontal is not None else [])
    total_w = sum(wt for _, wt in parts)
    # média geométrica ponderada: qualquer componente muito fraca puxa o score para baixo
    score = float(np.exp(sum(wt * np.log(max(v, 1e-3)) for v, wt in parts) / total_w))
    return Quality(score=score, sharpness=sharp, size=size, frontal=frontal)
