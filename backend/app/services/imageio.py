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


class AsyncJpegWriter:
    """Codifica e grava JPEG num pequeno pool de threads (o OpenCV liberta o GIL): a análise não espera pela
    escrita de cada recorte/frame. As imagens passadas não podem ser alteradas depois (passar cópias).

    `flush()` espera que tudo esteja gravado e levanta o primeiro erro; chama-se antes de cada commit, para que
    nenhuma deteção fique registada sem os seus ficheiros. Limita o n.º de escritas pendentes (memória)."""

    def __init__(self, workers: int = 2, max_pending: int = 64) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="faceai-jpeg")
        self._pending: list = []
        self._max_pending = max_pending

    def submit(self, path: str | Path, image: np.ndarray, quality: int = 92) -> None:
        if len(self._pending) >= self._max_pending:
            self._pending.pop(0).result()
        self._pending.append(self._pool.submit(write_jpeg, path, image, quality))

    def flush(self) -> None:
        pending, self._pending = self._pending, []
        error = None
        for f in pending:
            try:
                f.result()
            except Exception as exc:  # espera por todas antes de levantar
                error = error or exc
        if error is not None:
            raise error

    def close(self) -> None:
        try:
            self.flush()
        finally:
            self._pool.shutdown(wait=True)
