"""OCR da data/hora sobreposta nos vídeos de CCTV (RapidOCR: PP-OCR em ONNX Runtime, modelos incluídos no pacote)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .clock import ClockSample, fit_clock, parse_datetime

log = logging.getLogger(__name__)

_lock = threading.Lock()
_engine = None


class OcrUnavailable(RuntimeError):
    pass


def _ocr():
    global _engine
    with _lock:
        if _engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError as exc:  # pragma: no cover - depende da instalação
                raise OcrUnavailable("OCR indisponível: instale o pacote 'rapidocr'.") from exc
            _engine = RapidOCR(params={"Global.log_level": "error"})
        return _engine


@dataclass(frozen=True)
class TextBox:
    text: str
    score: float
    x: int
    y: int
    w: int
    h: int


def _run(image: np.ndarray, *, detect: bool = True) -> list[TextBox]:
    eng = _ocr()
    with _lock:
        res = eng(image, use_det=detect, use_cls=False, use_rec=True)
    txts = list(res.txts or ())
    scores = list(res.scores or ())
    if not detect:
        h, w = image.shape[:2]
        return [TextBox(t, float(s), 0, 0, w, h) for t, s in zip(txts, scores, strict=False)]
    boxes = [] if res.boxes is None else list(res.boxes)
    out = []
    for t, s, b in zip(txts, scores, boxes, strict=False):
        pts = np.asarray(b)
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        out.append(TextBox(t, float(s), int(x0), int(y0), int(x1 - x0), int(y1 - y0)))
    return out


def read_frame(path: str | Path, t: float, fps: float) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    try:
        if t > 0 and fps > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        raise ValueError(f"Não foi possível ler o frame em t={t:.2f} s.")
    return frame


def _lines(boxes: list[TextBox]) -> list[list[TextBox]]:
    """Agrupa as caixas de texto em linhas (o DVR pode separar data e hora em blocos distintos)."""
    lines: list[list[TextBox]] = []
    for b in sorted(boxes, key=lambda b: (b.y + b.h / 2, b.x)):
        cy = b.y + b.h / 2
        for line in lines:
            ref = line[0]
            if abs(cy - (ref.y + ref.h / 2)) <= max(ref.h, b.h) * 0.6:
                line.append(b)
                break
        else:
            lines.append([b])
    return [sorted(line, key=lambda b: b.x) for line in lines]


def _union(boxes: list[TextBox]) -> tuple[int, int, int, int]:
    x0 = min(b.x for b in boxes)
    y0 = min(b.y for b in boxes)
    x1 = max(b.x + b.w for b in boxes)
    y1 = max(b.y + b.h for b in boxes)
    return x0, y0, x1 - x0, y1 - y0


def find_timestamp(image: np.ndarray, date_order: str = "DMY") -> dict | None:
    """Procura no frame inteiro o texto com data e hora. Devolve a zona (normalizada, com margem) e a leitura."""
    boxes = _run(image)
    candidates: list[tuple[list[TextBox], str]] = [([b], b.text) for b in boxes]
    lines = _lines(boxes)
    candidates += [(line, " ".join(b.text for b in line)) for line in lines if len(line) > 1]
    # data e hora em duas linhas seguidas (ex.: data por cima da hora)
    for a, b in zip(lines, lines[1:], strict=False):
        candidates.append((a + b, " ".join(x.text for x in a + b)))
    for group, text in candidates:
        dvr = parse_datetime(text, date_order)
        if dvr is not None:
            H, W = image.shape[:2]
            x, y, w, h = _union(group)
            pad_x, pad_y = int(w * 0.08) + 4, int(h * 0.35) + 4
            x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
            x1, y1 = min(W, x + w + pad_x), min(H, y + h + pad_y)
            roi = [x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H]
            return {"roi": [round(v, 5) for v in roi], "text": text, "dvr": dvr}
    return None


def crop_roi(image: np.ndarray, roi: list[float]) -> np.ndarray:
    H, W = image.shape[:2]
    x, y, w, h = roi
    x0, y0 = int(max(0.0, x) * W), int(max(0.0, y) * H)
    x1, y1 = int(min(1.0, x + w) * W), int(min(1.0, y + h) * H)
    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        raise ValueError("Zona de OCR vazia.")
    if crop.shape[0] < 40:  # texto pequeno (CIF/D1): ampliar ajuda o reconhecimento
        scale = 48 / crop.shape[0]
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return crop


def read_timestamp(image: np.ndarray, roi: list[float], date_order: str = "DMY") -> tuple[str, object]:
    """Lê a zona indicada: primeiro só reconhecimento (rápido, 1 linha); se não der uma data/hora, deteção +
    reconhecimento dentro da zona (data e hora em blocos ou linhas separadas)."""
    crop = crop_roi(image, roi)
    texts = [b.text for b in _run(crop, detect=False)]
    text = " ".join(texts)
    dvr = parse_datetime(text, date_order)
    if dvr is None:
        boxes = _run(crop)
        text = " ".join(b.text for line in _lines(boxes) for b in line)
        dvr = parse_datetime(text, date_order)
    return text, dvr


def calibrate(path: str | Path, fps: float, duration_s: float, *, roi: list[float] | None, samples: int,
              date_order: str) -> dict:
    """Lê a data/hora em `samples` frames distribuídos pelo vídeo e ajusta o relógio."""
    if roi is None:
        for t in (0.0, min(1.0, duration_s / 2), duration_s / 2):
            found = find_timestamp(read_frame(path, t, fps), date_order)
            if found:
                roi = found["roi"]
                break
        else:
            raise ValueError("Não foi encontrada nenhuma data/hora na imagem. Marque a zona manualmente.")
    n = max(2, samples)
    end = max(0.0, duration_s - 1.0)
    times = sorted({round(end * k / (n - 1), 3) for k in range(n)}) if end > 0 else [0.0]
    readings: list[ClockSample] = []
    for t in times:
        try:
            text, dvr = read_timestamp(read_frame(path, t, fps), roi, date_order)
        except ValueError as exc:
            text, dvr = f"(erro: {exc})", None
        readings.append(ClockSample(video_s=t, text=text, dvr=dvr))
    result = {
        "roi": roi, "date_order": date_order,
        "samples": [{"video_s": r.video_s, "text": r.text, "dvr": r.dvr.isoformat() if r.dvr else None}
                    for r in readings],
    }
    try:
        fit = fit_clock(readings)
    except ValueError as exc:
        result["error"] = str(exc)
        return result
    for i, smp in enumerate(result["samples"]):
        smp["inlier"] = i in fit.inliers
    result.update({"dvr_start": fit.start.isoformat(timespec="milliseconds"), "rate": round(fit.rate, 6),
                   "max_residual_s": fit.max_residual_s, "inliers": len(fit.inliers),
                   "valid_readings": sum(1 for r in readings if r.dvr is not None)})
    return result
