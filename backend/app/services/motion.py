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


MASK_MARGIN = 0.25  # alarga a região do relógio (fração do seu tamanho) para cobrir dígitos que variam de largura


class MotionGate:
    """`mask_roi` ([x, y, w, h] em frações do frame, ex.: a região da hora do DVR lida por OCR) é ignorada na
    comparação: um relógio grande sobreposto à imagem muda a cada segundo e faria todos os frames parecer ter
    movimento."""

    def __init__(self, threshold: float, keyframe_interval_s: float, mask_roi: list[float] | None = None):
        self.threshold = threshold
        self.keyframe_interval_s = keyframe_interval_s
        self.mask_roi = mask_roi
        self._reference: np.ndarray | None = None
        self._last_analyzed_s: float | None = None

    def _small(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (WORK_WIDTH, max(1, int(h * WORK_WIDTH / w))), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        if self.mask_roi:
            sh, sw = gray.shape[:2]
            x, y, rw, rh = self.mask_roi
            mx, my = rw * MASK_MARGIN, rh * MASK_MARGIN
            x0, y0 = max(0, int((x - mx) * sw)), max(0, int((y - my) * sh))
            x1, y1 = min(sw, int(np.ceil((x + rw + mx) * sw))), min(sh, int(np.ceil((y + rh + my) * sh)))
            gray[y0:y1, x0:x1] = 0
        return gray

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
