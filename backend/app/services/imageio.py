"""Leitura/escrita de imagens com suporte a caminhos com caracteres não ASCII."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def read_image(path: str | Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Ficheiro de imagem inválido ou formato não suportado.")
    return img


def write_jpeg(path: str | Path, image: np.ndarray, quality: int = 92) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError(f"Falha ao codificar a imagem {path.name}")
    buf.tofile(str(path))
