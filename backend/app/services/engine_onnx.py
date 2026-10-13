"""Motor ONNX Runtime: deteção SCRFD + reconhecimento ArcFace (modelos InsightFace).

- Sem TensorFlow: arranque e inferência rápidos em CPU; GPU NVIDIA com `onnxruntime-gpu`.
- Os pesos são descarregados uma vez dos *releases* do InsightFace e verificados por SHA-256 (fixado abaixo).
- O pós-processamento do SCRFD e o alinhamento ArcFace seguem a implementação de referência do InsightFace
  (código MIT). Os **pesos** pré-treinados do InsightFace são só para uso não comercial/investigação.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import settings
from .face_engine import FaceObservation, ModelInfo

log = logging.getLogger(__name__)

_RELEASE = "https://github.com/deepinsight/insightface/releases/download/v0.7"


@dataclass(frozen=True)
class ModelFile:
    pack: str
    filename: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{_RELEASE}/{self.pack}.zip"


DETECTORS: dict[str, ModelFile] = {
    "scrfd_10g": ModelFile("buffalo_l", "det_10g.onnx",
                           "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91"),
    "scrfd_500m": ModelFile("buffalo_sc", "det_500m.onnx",
                            "5e4447f50245bbd7966bd6c0fa52938c61474a04ec7def48753668a9d8b4ea3a"),
}
RECOGNIZERS: dict[str, ModelFile] = {
    "arcface_r50": ModelFile("buffalo_l", "w600k_r50.onnx",
                             "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43"),
    "arcface_mbf": ModelFile("buffalo_sc", "w600k_mbf.onnx",
                             "9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f"),
}
MODEL_INFO = [
    ModelInfo("arcface_r50", 0.65, "ArcFace ResNet-50 (WebFace600K) — mais preciso"),
    ModelInfo("arcface_mbf", 0.65, "ArcFace MobileFaceNet (WebFace600K) — mais rápido"),
]

DET_THRESHOLD = 0.5
FAST_DET_THRESHOLD = 0.35  # mais permissivo: a 1.ª passagem não deve perder faces
NMS_THRESHOLD = 0.4
BASE_INPUT = 640


# ----------------------------------------------------------------------------- download / verificação
def models_dir() -> Path:
    return settings.models_dir


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_download_lock = threading.Lock()


def ensure_model(mf: ModelFile) -> Path:
    """Devolve o caminho do ficheiro ONNX, descarregando e verificando o pacote se necessário."""
    target = models_dir() / mf.pack / mf.filename
    with _download_lock:
        if target.exists() and sha256_file(target) == mf.sha256:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        log.info("A descarregar %s (%s)…", mf.pack, mf.url)
        with tempfile.TemporaryDirectory(dir=models_dir()) as tmp:
            zpath = Path(tmp) / f"{mf.pack}.zip"
            with urllib.request.urlopen(mf.url, timeout=60) as resp, zpath.open("wb") as out:
                shutil.copyfileobj(resp, out, length=1 << 20)
            with zipfile.ZipFile(zpath) as zf:
                for info in zf.infolist():
                    name = Path(info.filename).name
                    if not name.endswith(".onnx"):
                        continue
                    wanted = {m.filename: m for m in (*DETECTORS.values(), *RECOGNIZERS.values())
                              if m.pack == mf.pack}
                    if name not in wanted:
                        continue
                    dest = target.parent / name
                    with zf.open(info) as src, dest.open("wb") as dst:
                        shutil.copyfileobj(src, dst, length=1 << 20)
                    if sha256_file(dest) != wanted[name].sha256:
                        dest.unlink(missing_ok=True)
                        raise RuntimeError(f"SHA-256 inválido para {name}: ficheiro descartado.")
        if not target.exists():
            raise RuntimeError(f"{mf.filename} não encontrado no pacote {mf.pack}.")
        return target


_dlls_loaded = False


def _providers() -> list[str]:
    import onnxruntime as ort

    global _dlls_loaded
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" in available and not _dlls_loaded and hasattr(ort, "preload_dlls"):
        # onnxruntime-gpu[cuda,cudnn]: carrega as bibliotecas CUDA/cuDNN instaladas por pip
        try:
            ort.preload_dlls()
        except Exception as exc:  # pragma: no cover - depende do hardware
            log.warning("Não foi possível pré-carregar as bibliotecas CUDA: %s", exc)
        _dlls_loaded = True
    return [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in available]


def _session(path: Path):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=opts, providers=_providers())


# ----------------------------------------------------------------------------- SCRFD
def _distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    return np.stack([points[:, 0] - distance[:, 0], points[:, 1] - distance[:, 1],
                     points[:, 0] + distance[:, 2], points[:, 1] + distance[:, 3]], axis=-1)


def _distance2kps(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    preds = []
    for i in range(0, distance.shape[1], 2):
        preds.append(points[:, i % 2] + distance[:, i])
        preds.append(points[:, i % 2 + 1] + distance[:, i + 1])
    return np.stack(preds, axis=-1)


def _nms(dets: np.ndarray, thresh: float) -> list[int]:
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1, yy1 = np.maximum(x1[i], x1[order[1:]]), np.maximum(y1[i], y1[order[1:]])
        xx2, yy2 = np.minimum(x2[i], x2[order[1:]]), np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thresh)[0] + 1]
    return keep


class SCRFD:
    def __init__(self, path: Path):
        self.session = _session(path)
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        n = len(self.output_names)
        if n not in (6, 9):
            raise ValueError(f"Modelo SCRFD inesperado ({n} saídas)")
        self.fmc, self.strides, self.num_anchors, self.use_kps = 3, (8, 16, 32), 2, n == 9
        self._centers: dict[tuple[int, int, int], np.ndarray] = {}

    def detect(self, img: np.ndarray, input_size: int = BASE_INPUT, thresh: float = DET_THRESHOLD
               ) -> tuple[np.ndarray, np.ndarray | None]:
        """Devolve (N×5 [x1,y1,x2,y2,score], N×5×2 landmarks) em coordenadas da imagem original."""
        h, w = img.shape[:2]
        scale = min(input_size / h, input_size / w)
        new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
        canvas = np.zeros((input_size, input_size, 3), dtype=np.uint8)
        canvas[:new_h, :new_w] = cv2.resize(img, (new_w, new_h))
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128, (input_size, input_size), (127.5, 127.5, 127.5),
                                     swapRB=True)
        outs = self.session.run(self.output_names, {self.input_name: blob})

        scores_l, boxes_l, kps_l = [], [], []
        for idx, stride in enumerate(self.strides):
            scores = outs[idx].reshape(-1)
            boxes = outs[idx + self.fmc].reshape(-1, 4) * stride
            gh, gw = input_size // stride, input_size // stride
            key = (gh, gw, stride)
            centers = self._centers.get(key)
            if centers is None:
                centers = np.stack(np.mgrid[:gh, :gw][::-1], axis=-1).astype(np.float32)
                centers = (centers * stride).reshape(-1, 2)
                centers = np.stack([centers] * self.num_anchors, axis=1).reshape(-1, 2)
                self._centers[key] = centers
            pos = np.where(scores >= thresh)[0]
            scores_l.append(scores[pos])
            boxes_l.append(_distance2bbox(centers, boxes)[pos])
            if self.use_kps:
                kps = outs[idx + self.fmc * 2].reshape(-1, 10) * stride
                kps_l.append(_distance2kps(centers, kps).reshape(-1, 5, 2)[pos])

        scores = np.concatenate(scores_l)
        if scores.size == 0:
            return np.zeros((0, 5), np.float32), (np.zeros((0, 5, 2), np.float32) if self.use_kps else None)
        boxes = np.concatenate(boxes_l) / scale
        dets = np.hstack([boxes, scores[:, None]]).astype(np.float32)
        keep = _nms(dets, NMS_THRESHOLD)
        kps = (np.concatenate(kps_l) / scale)[keep] if self.use_kps else None
        return dets[keep], kps


# ----------------------------------------------------------------------------- ArcFace
ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def _umeyama(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Transformação de semelhança (rotação + escala + translação) por mínimos quadrados (Umeyama, 1991)."""
    num, dim = src.shape
    src_mean, dst_mean = src.mean(0), dst.mean(0)
    src_d, dst_d = src - src_mean, dst - dst_mean
    a = dst_d.T @ src_d / num
    d = np.ones(dim)
    if np.linalg.det(a) < 0:
        d[-1] = -1
    u, s, vt = np.linalg.svd(a)
    rot = u @ np.diag(d) @ vt
    scale = (s @ d) / src_d.var(0).sum()
    m = np.zeros((2, 3), dtype=np.float32)
    m[:, :2] = scale * rot
    m[:, 2] = dst_mean - scale * (rot @ src_mean)
    return m


def align_face(img: np.ndarray, landmarks: np.ndarray, size: int = 112) -> np.ndarray:
    m = _umeyama(landmarks.astype(np.float64), ARCFACE_DST.astype(np.float64) * (size / 112.0))
    return cv2.warpAffine(img, m, (size, size), borderValue=0.0)


class ArcFace:
    def __init__(self, path: Path):
        self.session = _session(path)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def embed(self, aligned: list[np.ndarray]) -> np.ndarray:
        blob = cv2.dnn.blobFromImages(aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        return self.session.run([self.output_name], {self.input_name: blob})[0].astype(np.float32)


# ----------------------------------------------------------------------------- motor
class OnnxEngine:
    name = "onnx"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._detectors: dict[str, SCRFD] = {}
        self._recognizers: dict[str, ArcFace] = {}

    def _detector(self, name: str) -> SCRFD:
        if name not in DETECTORS:
            raise ValueError(f"Detetor desconhecido: {name}")
        with self._lock:
            if name not in self._detectors:
                self._detectors[name] = SCRFD(ensure_model(DETECTORS[name]))
            return self._detectors[name]

    def _recognizer(self, name: str) -> ArcFace:
        if name not in RECOGNIZERS:
            raise ValueError(f"Modelo desconhecido: {name}")
        with self._lock:
            if name not in self._recognizers:
                self._recognizers[name] = ArcFace(ensure_model(RECOGNIZERS[name]))
            return self._recognizers[name]

    @staticmethod
    def _input_size(upscale: float) -> int:
        return max(160, int(round(BASE_INPUT * max(1.0, upscale or 1.0) / 32)) * 32)

    def detect(
        self, image_bgr: np.ndarray, *, model: str, detector: str, upscale: float = 1.0
    ) -> list[FaceObservation]:
        dets, kps = self._detector(detector).detect(image_bgr, self._input_size(upscale), DET_THRESHOLD)
        if len(dets) == 0 or kps is None:
            return []
        aligned = [align_face(image_bgr, k) for k in kps]
        embeddings = self._recognizer(model).embed(aligned)
        h, w = image_bgr.shape[:2]
        faces = []
        for det, k, emb in zip(dets, kps, embeddings, strict=True):
            x1, y1 = max(0, int(det[0])), max(0, int(det[1]))
            x2, y2 = min(w, int(round(det[2]))), min(h, int(round(det[3])))
            if x2 <= x1 or y2 <= y1:
                continue
            faces.append(FaceObservation(x=x1, y=y1, w=x2 - x1, h=y2 - y1, confidence=float(det[4]),
                                         embedding=emb, landmarks=k.astype(np.float32)))
        return faces

    def count_faces(self, image_bgr: np.ndarray, *, detector: str) -> int:
        dets, _ = self._detector(detector).detect(image_bgr, BASE_INPUT, FAST_DET_THRESHOLD)
        return len(dets)

    def warmup(self) -> None:
        """Garante (download + SHA-256) e carrega os modelos por omissão e o detetor rápido."""
        self._detector("scrfd_10g")
        self._detector("scrfd_500m")
        self._recognizer("arcface_r50")

    def models(self) -> list[ModelInfo]:
        return MODEL_INFO

    def detectors(self) -> list[str]:
        return list(DETECTORS)

    def default_fast_detector(self) -> str | None:
        return "scrfd_500m"

    def environment(self) -> dict[str, Any]:
        return _environment()


@lru_cache(maxsize=1)
def _environment() -> dict[str, Any]:
    env: dict[str, Any] = {
        "engine": "onnx",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "opencv": cv2.__version__,
        "numpy": np.__version__,
        "models_sha256": {k: v.sha256 for k, v in {**DETECTORS, **RECOGNIZERS}.items()},
    }
    try:
        import onnxruntime as ort

        providers = _providers()
        env["onnxruntime"] = ort.__version__
        env["providers"] = providers
        env["device"] = "GPU" if "CUDAExecutionProvider" in providers else "CPU"
    except Exception as exc:  # pragma: no cover - depende da instalação
        env["onnxruntime"] = f"indisponível ({exc})"
        env["device"] = "CPU"
    return env
