"""Deteção de pessoas (YOLOX) e embedding de aparência do corpo (OSNet-AIN ReID), em ONNX Runtime.

Usado pela análise complementar por vestuário: a mesma pessoa, na mesma visita, veste a mesma roupa em todas as
câmaras do estabelecimento — mesmo de costas ou longe, quando a face não serve.

- YOLOX (Megvii, Apache-2.0): modelos ONNX oficiais dos releases do projeto; classe 0 do COCO = pessoa.
- OSNet-AIN x1.0 treinado no MSMT17 (torchreid, MIT), exportado para ONNX por tools/export_reid_onnx.py e
  publicado no release "models-reid-v1" deste repositório.
Todos os ficheiros são verificados por SHA-256 (fixado abaixo) antes de serem usados.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from .engine_onnx import _nms, _session, models_dir, sha256_file

log = logging.getLogger(__name__)

_YOLOX = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0"
_REID = "https://github.com/miguelsilv6/FaceAI/releases/download/models-reid-v1"


@dataclass(frozen=True)
class RemoteModel:
    url: str
    filename: str
    sha256: str
    input_size: int = 0


PERSON_DETECTORS: dict[str, RemoteModel] = {
    "yolox_s": RemoteModel(f"{_YOLOX}/yolox_s.onnx", "yolox_s.onnx",
                           "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063", 640),
    "yolox_tiny": RemoteModel(f"{_YOLOX}/yolox_tiny.onnx", "yolox_tiny.onnx",
                              "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7", 416),
    "yolox_nano": RemoteModel(f"{_YOLOX}/yolox_nano.onnx", "yolox_nano.onnx",
                              "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d", 416),
}
REID_MODEL = RemoteModel(f"{_REID}/osnet_ain_x1_0_msmt17.onnx", "osnet_ain_x1_0_msmt17.onnx",
                         "14691a6c452d47dede29fb3278b3406c76017e9c0d0d1b2f624e501103712d09")
DEFAULT_PERSON_DETECTOR = "yolox_s"
# Distância de cosseno máxima (OSNet-AIN): em 30 pessoas × degradações de CCTV (resolução, desfoque, JPEG,
# tom, espelho), 0,27 deu ~0,06 % de falsos positivos entre pessoas diferentes. Ajustado no próprio caso pelos
# corpos cuja face é de outra pessoa (ver body_analysis.finalize).
DEFAULT_REID_THRESHOLD = 0.27
PERSON_THRESHOLD = 0.5
REID_SIZE = (128, 256)  # largura × altura
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_download_lock = threading.Lock()


def ensure_file(m: RemoteModel, subdir: str) -> Path:
    target = models_dir() / subdir / m.filename
    with _download_lock:
        if target.exists() and sha256_file(target) == m.sha256:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        log.info("A descarregar %s…", m.url)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, suffix=".part") as tmp:
            with urllib.request.urlopen(m.url, timeout=120) as resp:
                shutil.copyfileobj(resp, tmp, length=1 << 20)
        part = Path(tmp.name)
        if sha256_file(part) != m.sha256:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"SHA-256 inválido para {m.filename}: ficheiro descartado.")
        part.replace(target)
        return target


@dataclass(frozen=True)
class PersonObservation:
    x: int
    y: int
    w: int
    h: int
    confidence: float


class BodyEngine(Protocol):
    def detect_persons(self, image_bgr: np.ndarray, *, detector: str, min_confidence: float = PERSON_THRESHOLD
                       ) -> list[PersonObservation]: ...

    def embed(self, crops: list[np.ndarray]) -> np.ndarray: ...

    def detectors(self) -> list[str]: ...

    def environment(self) -> dict[str, Any]: ...


# ----------------------------------------------------------------------------- YOLOX
def _yolox_grids(size: int) -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for s in (8, 16, 32):
        n = size // s
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        g = np.stack((xv, yv), 2).reshape(-1, 2)
        grids.append(g)
        strides.append(np.full((g.shape[0], 1), s))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


class YoloxPersonDetector:
    def __init__(self, path: Path, input_size: int):
        self.session = _session(path)
        self.input_name = self.session.get_inputs()[0].name
        self.size = input_size
        self.grids, self.strides = _yolox_grids(input_size)

    def detect(self, img: np.ndarray, thresh: float = PERSON_THRESHOLD) -> np.ndarray:
        """N×5 [x1, y1, x2, y2, score] das pessoas, em coordenadas da imagem original."""
        h, w = img.shape[:2]
        r = min(self.size / h, self.size / w)
        canvas = np.full((self.size, self.size, 3), 114, dtype=np.uint8)
        nh, nw = max(1, int(h * r)), max(1, int(w * r))
        canvas[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = canvas.transpose(2, 0, 1)[None].astype(np.float32)  # BGR, 0–255 (pré-processamento do YOLOX)
        out = self.session.run(None, {self.input_name: blob})[0][0]
        xy = (out[:, :2] + self.grids) * self.strides
        wh = np.exp(np.clip(out[:, 2:4], -10, 10)) * self.strides
        scores = out[:, 4] * out[:, 5]  # objectness × classe "pessoa"
        keep = scores >= thresh
        if not keep.any():
            return np.zeros((0, 5), dtype=np.float32)
        xy, wh, scores = xy[keep], wh[keep], scores[keep]
        boxes = np.concatenate([xy - wh / 2, xy + wh / 2], axis=1) / r
        dets = np.hstack([boxes, scores[:, None]]).astype(np.float32)
        dets = dets[_nms(dets, 0.45)]
        dets[:, [0, 2]] = dets[:, [0, 2]].clip(0, w - 1)
        dets[:, [1, 3]] = dets[:, [1, 3]].clip(0, h - 1)
        return dets


class OsnetEmbedder:
    def __init__(self, path: Path):
        self.session = _session(path)
        self.input_name = self.session.get_inputs()[0].name

    def embed(self, crops: list[np.ndarray]) -> np.ndarray:
        if not crops:
            return np.zeros((0, 512), dtype=np.float32)
        batch = np.stack([cv2.resize(c, REID_SIZE, interpolation=cv2.INTER_LINEAR) for c in crops])
        batch = (batch[..., ::-1].astype(np.float32) / 255.0 - _MEAN) / _STD  # BGR→RGB, normalização ImageNet
        feats = self.session.run(None, {self.input_name: batch.transpose(0, 3, 1, 2).copy()})[0]
        norms = np.linalg.norm(feats, axis=1, keepdims=True)
        return feats / np.maximum(norms, 1e-12)


class OnnxBodyEngine:
    name = "onnx-body"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._detectors: dict[str, YoloxPersonDetector] = {}
        self._embedder: OsnetEmbedder | None = None

    def _detector(self, name: str) -> YoloxPersonDetector:
        if name not in self._detectors:
            m = PERSON_DETECTORS[name]
            self._detectors[name] = YoloxPersonDetector(ensure_file(m, "yolox"), m.input_size)
        return self._detectors[name]

    def detect_persons(self, image_bgr: np.ndarray, *, detector: str, min_confidence: float = PERSON_THRESHOLD
                       ) -> list[PersonObservation]:
        with self._lock:
            dets = self._detector(detector).detect(image_bgr, thresh=min_confidence)
        return [PersonObservation(int(x1), int(y1), max(1, int(x2 - x1)), max(1, int(y2 - y1)), float(s))
                for x1, y1, x2, y2, s in dets]

    def embed(self, crops: list[np.ndarray]) -> np.ndarray:
        with self._lock:
            if self._embedder is None:
                self._embedder = OsnetEmbedder(ensure_file(REID_MODEL, "reid"))
            return self._embedder.embed(crops)

    def detectors(self) -> list[str]:
        return list(PERSON_DETECTORS)

    def environment(self) -> dict[str, Any]:
        return _environment()


@lru_cache(maxsize=1)
def _environment() -> dict[str, Any]:
    return {
        "person_detectors_sha256": {k: v.sha256 for k, v in PERSON_DETECTORS.items()},
        "reid_model": REID_MODEL.filename,
        "reid_sha256": REID_MODEL.sha256,
    }


_engine: BodyEngine | None = None
_engine_lock = threading.Lock()


def get_body_engine() -> BodyEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = OnnxBodyEngine()
        return _engine


def set_body_engine(engine: BodyEngine | None) -> None:
    global _engine
    with _engine_lock:
        _engine = engine
