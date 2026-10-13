"""Extração do frame original (sem anotações) de uma deteção, para download.

A análise numera os frames lendo o vídeo sequencialmente. Saltar diretamente para o frame N (seek) é rápido
mas pode falhar por alguns frames em ficheiros de DVR com marcas de tempo irregulares. Por isso o frame obtido é
verificado contra o recorte da face guardado durante a análise (que não tem anotações): se não coincidir,
procuram-se os frames vizinhos e, se nenhum (ou mais de um) coincidir, lê-se o vídeo sequencialmente até ao
frame N, exatamente como a análise (mais lento em vídeos longos). O método usado fica registado na auditoria.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .imageio import read_image
from .video_processor import crop_with_margin

MAX_CROP_ERROR = 4.0  # erro absoluto médio (0–255) tolerado entre o recorte guardado (JPEG) e o frame lido
NEIGHBOURS = 3


@dataclass(frozen=True)
class ExtractedFrame:
    image: np.ndarray
    method: str  # "seek" | "vizinho" | "sequencial" | "seek (sem verificação)"
    crop_error: float | None  # erro do recorte; None = recorte indisponível


def crop_error(frame: np.ndarray, expected_crop: np.ndarray, bbox: tuple[int, int, int, int],
               margin: float = 0.25) -> float:
    crop = crop_with_margin(frame, *bbox, margin=margin)
    if crop.shape != expected_crop.shape:
        return float("inf")
    return float(np.abs(crop.astype(np.int16) - expected_crop.astype(np.int16)).mean())


def _read_at(cap: cv2.VideoCapture, index: int) -> np.ndarray | None:
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = cap.read()
    return frame if ok else None


def _read_range(cap: cv2.VideoCapture, start: int, count: int) -> list[np.ndarray]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    out = []
    for _ in range(count):
        ok, frame = cap.read()
        if not ok:
            break
        out.append(frame)
    return out


def _read_sequential(path: str, index: int) -> np.ndarray | None:
    cap = cv2.VideoCapture(path)
    try:
        for _ in range(index):
            if not cap.grab():
                return None
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def extract_frame(video_path: str | Path, frame_index: int, crop_path: str | Path | None,
                  bbox: tuple[int, int, int, int], margin: float = 0.25) -> ExtractedFrame:
    """margin: margem do recorte guardado (faces 0.25; corpos 0.05)."""
    path = str(video_path)
    expected = None
    if crop_path and Path(crop_path).exists():
        try:
            expected = read_image(crop_path)
        except ValueError:
            expected = None
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError("Não foi possível abrir o vídeo.")
    try:
        frame = _read_at(cap, frame_index)
        if expected is None:
            if frame is None:
                raise ValueError(f"Não foi possível ler o frame {frame_index}.")
            return ExtractedFrame(frame, "seek (sem verificação)", None)
        if frame is not None:
            err = crop_error(frame, expected, bbox, margin)
            if err <= MAX_CROP_ERROR:
                return ExtractedFrame(frame, "seek", round(err, 2))
        # Vizinhos: o seek pode ter caído alguns frames ao lado. Só se aceita um vizinho se for o único que
        # coincide; numa cena parada vários coincidem e não há forma de saber qual é o N → leitura sequencial.
        start = max(0, frame_index - NEIGHBOURS)
        found = [(err, f) for f in _read_range(cap, start, frame_index - start + NEIGHBOURS + 1)
                 if (err := crop_error(f, expected, bbox, margin)) <= MAX_CROP_ERROR]
        if len(found) == 1:
            return ExtractedFrame(found[0][1], "vizinho", round(found[0][0], 2))
    finally:
        cap.release()
    frame = _read_sequential(path, frame_index)
    if frame is None:
        raise ValueError(f"Não foi possível ler o frame {frame_index}.")
    return ExtractedFrame(frame, "sequencial", round(crop_error(frame, expected, bbox, margin), 2))
