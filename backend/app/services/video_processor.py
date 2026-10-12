"""Leitura de vídeo com OpenCV e amostragem de frames."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class VideoInfo:
    fps: float
    frame_count: int
    width: int
    height: int
    duration_s: float
    codec: str


def probe(path: str | Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("Não foi possível abrir o vídeo (formato/codec não suportado pelo OpenCV).")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
        codec = "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)).strip("\x00 ") if fourcc else ""
    finally:
        cap.release()
    if fps <= 0 or fps > 1000:
        fps = 25.0  # valor de recurso para ficheiros com metadados de fps inválidos
    duration = frame_count / fps if frame_count > 0 else 0.0
    return VideoInfo(fps=fps, frame_count=frame_count, width=width, height=height, duration_s=duration, codec=codec)


@dataclass(frozen=True)
class SampledFrame:
    index: int
    timestamp_s: float
    image: np.ndarray


def sample_frames(path: str | Path, sample_fps: float, fps: float | None = None) -> Iterator[SampledFrame]:
    """Devolve frames a ~`sample_fps` por segundo. Os frames intermédios são só descodificados com grab()."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Não foi possível abrir o vídeo: {path}")
    try:
        native_fps = fps or float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 25.0
        step = max(1, int(round(native_fps / sample_fps))) if sample_fps > 0 else 1
        index = 0
        while True:
            if not cap.grab():
                break
            if index % step == 0:
                ok, frame = cap.retrieve()
                if ok and frame is not None:
                    yield SampledFrame(index=index, timestamp_s=index / native_fps, image=frame)
            index += 1
    finally:
        cap.release()


def crop_with_margin(image: np.ndarray, x: int, y: int, w: int, h: int, margin: float = 0.25) -> np.ndarray:
    H, W = image.shape[:2]
    mx, my = int(w * margin), int(h * margin)
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(W, x + w + mx), min(H, y + h + my)
    return image[y0:y1, x0:x1].copy()


def annotate(image: np.ndarray, x: int, y: int, w: int, h: int, label: str, color=(0, 0, 255)) -> np.ndarray:
    out = image.copy()
    thickness = max(2, image.shape[1] // 400)
    cv2.rectangle(out, (x, y), (x + w, y + h), color, thickness)
    font_scale = max(0.5, image.shape[1] / 1600)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
    ty = max(th + 4, y - 6)
    cv2.rectangle(out, (x, ty - th - 4), (x + tw + 4, ty + 2), color, -1)
    cv2.putText(out, label, (x + 2, ty - 2), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def format_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
