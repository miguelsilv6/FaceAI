"""Pré-filtro de movimento: só analisa frames em que a imagem mudou.

Em CCTV a maior parte do vídeo é estática; comparar uma versão pequena e desfocada do frame com o último frame
analisado é muito mais barato do que correr a deteção facial. Um frame-chave periódico garante que cenas
paradas (ex.: alguém imóvel) continuam a ser verificadas.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

WORK_WIDTH = 160
PIXEL_DELTA = 25  # diferença de intensidade (0–255) para um píxel contar como "mudou"


@dataclass(frozen=True)
class MotionDecision:
    analyze: bool
    ratio: float
    reason: str  # "first" | "motion" | "keyframe" | "static"


class MotionGate:
    def __init__(self, threshold: float, keyframe_interval_s: float):
        self.threshold = threshold
        self.keyframe_interval_s = keyframe_interval_s
        self._reference: np.ndarray | None = None
        self._last_analyzed_s: float | None = None

    @staticmethod
    def _small(frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (WORK_WIDTH, max(1, int(h * WORK_WIDTH / w))), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        return cv2.GaussianBlur(gray, (5, 5), 0)

    def check(self, frame: np.ndarray, timestamp_s: float) -> MotionDecision:
        small = self._small(frame)
        if self._reference is None or self._reference.shape != small.shape:
            return self._accept(small, timestamp_s, 1.0, "first")
        ratio = float(np.count_nonzero(cv2.absdiff(small, self._reference) > PIXEL_DELTA)) / small.size
        if self.threshold <= 0 or ratio >= self.threshold:
            return self._accept(small, timestamp_s, ratio, "motion")
        if self.keyframe_interval_s > 0 and timestamp_s - (self._last_analyzed_s or 0.0) >= self.keyframe_interval_s:
            return self._accept(small, timestamp_s, ratio, "keyframe")
        return MotionDecision(False, ratio, "static")

    def _accept(self, small: np.ndarray, ts: float, ratio: float, reason: str) -> MotionDecision:
        self._reference = small
        self._last_analyzed_s = ts
        return MotionDecision(True, ratio, reason)
