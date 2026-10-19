"""Frame anotado de uma deteção, gerado a pedido a partir do vídeo (em vez de gravado por cada deteção).

Gravar um JPEG do frame inteiro por cada face era a maior fonte de crescimento das análises (num frame com 3
faces, 3 cópias do mesmo frame). O frame é reprodutível: vem do vídeo analisado pelo n.º de frame (lido e
verificado contra o recorte da face, como o PNG original) e a caixa está na base de dados. Fica em cache um JPEG
limpo por n.º de frame (partilhado pelas faces desse frame); a caixa e o rótulo são desenhados a cada pedido.
As deteções de análises anteriores continuam a servir o ficheiro gravado (Detection.frame_path).
"""

from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np
from sqlmodel import Session

from ..models import Detection, Job, Video
from .frames import extract_frame
from .imageio import read_image, write_jpeg
from .video_processor import annotate, format_timestamp

CACHE_DIR = "frames_cache"
QUALITY = 90
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def stored_frame(det: Detection) -> str | None:
    """Ficheiro do frame anotado gravado pela análise (análises anteriores a esta versão)."""
    return det.frame_path if det.frame_path and Path(det.frame_path).exists() else None


def _lock(key: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(key, threading.Lock())


def clean_frame(job: Job, video: Video, det: Detection) -> np.ndarray:
    """Frame do vídeo (sem marcações) em cache, um por n.º de frame."""
    from .jobs import job_dir  # import local (jobs importa muitos serviços)

    cache = job_dir(job) / CACHE_DIR / f"v{video.id:04d}_f{det.frame_index:07d}.jpg"
    with _lock(str(cache)):
        if cache.exists():
            return read_image(cache)
        if not Path(video.source_path).exists():
            raise ValueError("o vídeo desta deteção já não está disponível")
        image = extract_frame(video.source_path, det.frame_index, det.crop_path, (det.x, det.y, det.w, det.h)).image
        cache.parent.mkdir(parents=True, exist_ok=True)
        part = cache.with_name(cache.stem + ".part.jpg")
        write_jpeg(part, image, QUALITY)
        part.replace(cache)
        return image


def label_and_color(job: Job, det: Detection) -> tuple[str, tuple[int, int, int]]:
    """O mesmo rótulo e a mesma cor que a análise desenhava (tempo, semelhança e qualidade)."""
    q = det.quality or 0.0
    if (job.params or {}).get("mode") == "discover" or det.reference_id is None:
        return f"{format_timestamp(det.timestamp_s)} q={q:.2f}", (255, 160, 0)
    return (f"{format_timestamp(det.timestamp_s)} sim={det.similarity:.2f} q={q:.2f}",
            (0, 0, 255) if det.is_match else (0, 165, 255))


def annotated_image(job: Job, video: Video | None, det: Detection) -> np.ndarray:
    stored = stored_frame(det)
    if stored:
        return read_image(stored)
    if video is None:
        raise ValueError("o vídeo desta deteção já não existe")
    label, color = label_and_color(job, det)
    return annotate(clean_frame(job, video, det), det.x, det.y, det.w, det.h, label, color)


def annotated_jpeg(job: Job, video: Video | None, det: Detection, width: int | None = None) -> bytes:
    """JPEG do frame anotado (opcionalmente reduzido para miniatura)."""
    stored = stored_frame(det)
    if stored and width is None:
        return Path(stored).read_bytes()
    image = annotated_image(job, video, det)
    if width and image.shape[1] > width:
        image = cv2.resize(image, (width, round(image.shape[0] * width / image.shape[1])), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, QUALITY if width is None else 82])
    if not ok:
        raise ValueError("falha ao codificar o frame anotado")
    return buf.tobytes()


def for_detection(s: Session, det: Detection, width: int | None = None) -> bytes:
    job = s.get(Job, det.job_id)
    if job is None:
        raise ValueError("análise desta deteção em falta")
    return annotated_jpeg(job, s.get(Video, det.video_id), det, width)


def frame_name(det: Detection) -> str:
    """Nome do frame anotado no ZIP/CSV (igual ao das análises anteriores)."""
    if det.frame_path:
        return Path(det.frame_path).name
    return f"v{det.video_id:04d}_f{det.frame_index:07d}_d{det.id}.jpg"
