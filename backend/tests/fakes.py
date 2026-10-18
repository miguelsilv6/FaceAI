"""Motor facial falso e geradores de media sintéticos para testes rápidos (sem TensorFlow).

Cada "face" é um quadrado de cor sólida sobre fundo preto. O embedding é a cor normalizada,
por isso quadrados da mesma cor correspondem à mesma "pessoa".
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.services.face_engine import FaceObservation, ModelInfo

RED, BLUE, GREEN = (0, 0, 255), (255, 0, 0), (0, 255, 0)


class FakeEngine:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.count_calls = 0

    def count_faces(self, image_bgr, *, detector, upscale=1.0):
        self.last_count_upscale = upscale
        self.count_calls += 1
        return len(self._find(image_bgr))

    def models(self):
        return [ModelInfo("fake_model", 0.05, "motor de teste")]

    def detectors(self):
        return ["fake_det", "fake_fast"]

    def default_fast_detector(self):
        return "fake_fast"

    def detect(self, image_bgr, *, model, detector, upscale=1.0, min_confidence=None, prefilter=None):
        self.last_min_confidence = min_confidence
        self.calls += 1
        faces = self._find(image_bgr)
        if prefilter is not None:
            faces = [f for f in faces if prefilter(f.x, f.y, f.w, f.h, f.landmarks)]
        return faces

    def _find(self, image_bgr):
        mask = (image_bgr.max(axis=2) > 40).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        faces = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w < 8 or h < 8 or h > 1.5 * w:  # retângulos altos são corpos (FakeBodyEngine), não faces
                continue
            color = image_bgr[y + h // 2, x + w // 2].astype(np.float32)
            emb = np.concatenate([color, [300.0]])  # componente constante: cores diferentes ficam a distância ~0.4
            # landmarks frontais sintéticos (olhos, nariz, boca)
            lm = np.array([[x + 0.3 * w, y + 0.4 * h], [x + 0.7 * w, y + 0.4 * h], [x + 0.5 * w, y + 0.6 * h],
                           [x + 0.35 * w, y + 0.8 * h], [x + 0.65 * w, y + 0.8 * h]], dtype=np.float32)
            faces.append(FaceObservation(x=x, y=y, w=w, h=h, confidence=0.99, embedding=emb, landmarks=lm))
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


class FakeBodyEngine:
    """Corpos = retângulos altos (tronco + pernas de cores diferentes). A caixa da pessoa estende-se para cima, para
    incluir a cabeça (a "face" do FakeEngine, desenhada por cima do corpo). O embedding é a cor do tronco e das
    pernas, por isso a mesma roupa dá a mesma "pessoa"."""

    name = "fake-body"

    def detect_persons(self, image_bgr, *, detector, min_confidence=0.5):
        from app.services.body_engine import PersonObservation

        mask = (image_bgr.max(axis=2) > 40).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        persons = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if h <= 1.5 * w or h < 20:
                continue
            top = max(0, y - int(0.4 * h))
            persons.append(PersonObservation(x, top, w, y + h - top, 0.9))
        return persons

    def embed(self, crops):
        out = []
        for c in crops:
            h, w = c.shape[:2]
            torso = c[int(h * 0.5), w // 2].astype(np.float32)
            legs = c[int(h * 0.9), w // 2].astype(np.float32)
            v = np.concatenate([torso, legs, [200.0]])
            out.append(v / np.linalg.norm(v))
        return np.stack(out) if out else np.zeros((0, 7), np.float32)

    def detectors(self):
        return ["yolox_s", "yolox_nano"]

    def environment(self):
        return {"engine": "fake-body"}


def make_people_video(path: Path, people: list[dict], duration_s: float = 6.0, fps: int = 10) -> Path:
    """Vídeo com "pessoas": {x, start, end, torso, legs, head (cor da face ou None = de costas)}."""
    w, h = 320, 240
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(int(duration_s * fps)):
        t = i / fps
        frame = np.zeros((h, w, 3), np.uint8)
        for p in people:
            if p["start"] <= t < p["end"]:
                x = p["x"]
                if p.get("head"):
                    cv2.rectangle(frame, (x + 8, 40), (x + 32, 64), p["head"], -1)  # face 25×25
                cv2.rectangle(frame, (x, 70), (x + 40, 120), p["torso"], -1)
                cv2.rectangle(frame, (x, 121), (x + 40, 170), p["legs"], -1)
        writer.write(frame)
    writer.release()
    return path


def h264_access_units(data: bytes) -> list[tuple[bool, bytes]]:
    """Divide uma sequência H.264 Annex B em frames: [(é_chave, bytes)] (um slice por frame, sem B-frames)."""
    starts, i = [], data.find(b"\x00\x00\x01")  # start codes de 3 ou 4 bytes (o x264 usa os dois)
    while i >= 0:
        starts.append(i - 1 if i > 0 and data[i - 1] == 0 else i)
        i = data.find(b"\x00\x00\x01", i + 3)
    nals = [data[a:b] for a, b in zip(starts, [*starts[1:], len(data)], strict=True)]
    units: list[list[bytes]] = []
    has_slice = False
    for nal in nals:
        kind = nal[nal.index(b"\x00\x00\x01") + 3] & 0x1F
        if not units or (has_slice and kind in (1, 5, 6, 7, 9)):
            units.append([])
            has_slice = False
        units[-1].append(nal)
        has_slice = has_slice or kind in (1, 5)
    return [(any(n[n.index(b"\x00\x00\x01") + 3] & 0x1F == 5 for n in u), b"".join(u)) for u in units]


def sec_block(payload: bytes, key: bool, width: int, height: int, seq: int) -> bytes:
    """Bloco .sec: cabeçalho de 48 bytes (estrutura das exportações reais) + carga H.264."""
    head = bytearray(48)
    head[0:4] = b"\x00\xe0\x02\x03"
    head[4] = 1 if key else 2
    head[6:12] = bytes.fromhex("b4948f508181")
    head[12:20] = seq.to_bytes(8, "big")
    head[20] = 0x81
    head[21:24] = (48 + len(payload)).to_bytes(3, "big")
    head[24:28] = width.to_bytes(4, "big")
    head[28:32] = height.to_bytes(4, "big")
    return bytes(head) + payload


def make_sec(path: Path, source: Path, *, sub_source: Path | None = None, garbage_after: int | None = None) -> Path:
    """Converte um vídeo num .sec sintético (H.264 por ffmpeg). sub_source: segundo stream (outra resolução)
    intercalado; garbage_after: bytes corrompidos depois desse frame (para testar a ressincronização)."""
    import subprocess

    def encode(src: Path) -> tuple[list[tuple[bool, bytes]], int, int]:
        cap = cv2.VideoCapture(str(src))
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-c:v", "libx264", "-bf", "0", "-g", "10",
                              "-x264-params", "slices=1:aud=0", "-pix_fmt", "yuv420p", "-f", "h264", "-"],
                             check=True, capture_output=True).stdout
        return h264_access_units(raw), w, h

    main, w, h = encode(source)
    sub, sw, sh = encode(sub_source) if sub_source else ([], 0, 0)
    out = bytearray()
    for i, (key, payload) in enumerate(main):
        out += sec_block(payload, key, w, h, i)
        if i < len(sub):
            out += sec_block(sub[i][1], sub[i][0], sw, sh, i)
        if garbage_after is not None and i == garbage_after:
            out += b"\x13\x37" * 500
    path.write_bytes(bytes(out))
    return path
