"""Motor facial falso e geradores de media sintéticos para testes rápidos (sem TensorFlow).

Cada "face" é um quadrado de cor sólida sobre fundo preto. O embedding é a cor normalizada,
por isso quadrados da mesma cor correspondem à mesma "pessoa".
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.services.face_engine import FaceObservation

RED, BLUE, GREEN = (0, 0, 255), (255, 0, 0), (0, 255, 0)


class FakeEngine:
    def __init__(self) -> None:
        self.calls = 0

    def detect(self, image_bgr, *, model, detector, upscale=1.0):
        self.calls += 1
        mask = (image_bgr.max(axis=2) > 40).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        faces = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w < 8 or h < 8:
                continue
            color = image_bgr[y + h // 2, x + w // 2].astype(np.float32)
            emb = np.concatenate([color, [300.0]])  # componente constante: cores diferentes ficam a distância ~0.4
            faces.append(FaceObservation(x=x, y=y, w=w, h=h, confidence=0.99, embedding=emb))
        return faces

    def environment(self):
        return {"device": "CPU", "engine": "fake"}


def make_image(path: Path, color, size=(200, 200), n_faces: int = 1) -> Path:
    img = np.zeros((size[1], size[0], 3), np.uint8)
    for i in range(n_faces):
        x0 = 20 + i * 90
        cv2.rectangle(img, (x0, 50), (x0 + 60, 110), color, -1)
    cv2.imwrite(str(path), img)
    return path


def make_video(path: Path, timeline: list[tuple[float, float, tuple]], duration_s: float = 10.0, fps: int = 10) -> Path:
    """Cria um vídeo em que cada (início, fim, cor) desenha um quadrado nesse intervalo de tempo."""
    w, h = 320, 240
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(int(duration_s * fps)):
        t = i / fps
        frame = np.zeros((h, w, 3), np.uint8)
        for k, (start, end, color) in enumerate(timeline):
            if start <= t < end:
                x0 = 30 + k * 120
                cv2.rectangle(frame, (x0, 80), (x0 + 70, 150), color, -1)
        writer.write(frame)
    writer.release()
    return path
