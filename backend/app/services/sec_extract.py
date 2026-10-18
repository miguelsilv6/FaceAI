"""Fila da extração dos formatos proprietários (.sec) em segundo plano.

A extração de um .sec grande (ler o ficheiro, escrever o MP4, calcular os SHA-256) demora minutos: no pedido de
fim do upload ultrapassava o tempo dos proxies (ex.: 100 s do Cloudflare) e o browser perdia a resposta. Agora o
vídeo fica registado logo, com extraction_status "pending", e é extraído aqui, um de cada vez, com o progresso
guardado no vídeo. Uma falha fica registada (estado "failed", motivo e auditoria) e o original é mantido para se
poder tentar de novo. As extrações interrompidas por um reinício são retomadas no arranque.

Também refaz a cópia com outro fps (correção: services/fps_fix.py), aplicando a correção só no fim.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from ..db import get_engine
from ..models import Video
from . import sec_format
from .audit import log_event

log = logging.getLogger(__name__)

PENDING, RUNNING, READY, FAILED = "pending", "running", "ready", "failed"
ACTOR = "sistema"
PROGRESS_EVERY_S = 1.0

_queue: queue.Queue[int] = queue.Queue()
_queued: set[int] = set()
_cancelled: set[int] = set()
_lock = threading.Lock()
_worker: threading.Thread | None = None


def is_ready(video: Video) -> bool:
    """O vídeo pode ser descodificado (formatos normais, ou extração concluída)."""
    return not video.extraction_status or video.extraction_status == READY


def not_ready_reason(video: Video) -> str:
    if video.extraction_status in (PENDING, RUNNING):
        return f"o vídeo {video.filename} ainda está a ser extraído ({round(video.extraction_progress * 100)}%)"
    return f"a extração do vídeo {video.filename} falhou: {video.extraction_error or 'motivo desconhecido'}"


def request_extraction(s: Session, video: Video, *, correction: dict[str, Any] | None = None) -> None:
    """Põe o vídeo na fila. correction: pedido de fps (fps_fix) a aplicar quando a nova cópia estiver feita."""
    if correction is not None:
        video.extraction = {**(video.extraction or {}), "pending_fps": correction}
    video.extraction_status, video.extraction_progress, video.extraction_error = PENDING, 0.0, ""
    s.add(video)
    s.commit()
    _enqueue(video.id)


def cancel_extractions(video_ids: list[int]) -> None:
    with _lock:
        _cancelled.update(i for i in video_ids if i in _queued)


def resume_pending_extractions() -> None:
    with Session(get_engine()) as s:
        ids = list(s.exec(select(Video.id).where(Video.extraction_status.in_([PENDING, RUNNING]))))
    for vid in ids:
        _enqueue(vid)


def start_resume_pending_extractions() -> None:
    threading.Thread(target=resume_pending_extractions, name="faceai-extract-resume", daemon=True).start()


def _enqueue(video_id: int) -> None:
    global _worker
    with _lock:
        if video_id in _queued:
            return
        _queued.add(video_id)
        _queue.put(video_id)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, daemon=True, name="faceai-extract")
            _worker.start()


def _work() -> None:
    global _worker
    while True:
        try:
            video_id = _queue.get(timeout=60)
        except queue.Empty:
            with _lock:
                if _queue.empty():
                    _worker = None
                    return
            continue
        try:
            with _lock:
                skip = video_id in _cancelled
            if not skip:
                process(video_id)
        except Exception:  # noqa: BLE001
            log.exception("Falha inesperada na extração do vídeo %s", video_id)
        finally:
            with _lock:
                _queued.discard(video_id)
                _cancelled.discard(video_id)


def _set(video_id: int, **fields) -> None:
    with Session(get_engine()) as s:
        v = s.get(Video, video_id)
        if v is not None:
            for k, val in fields.items():
                setattr(v, k, val)
            s.add(v)
            s.commit()


def process(video_id: int) -> None:
    """Extrai (ou refaz com outro fps) a cópia de análise de um vídeo. Público para os testes."""
    with Session(get_engine(), expire_on_commit=False) as s:
        video = s.get(Video, video_id)
        if video is None or video.extraction_status not in (PENDING, RUNNING):
            return
        video.extraction_status, video.extraction_progress = RUNNING, 0.0
        s.add(video)
        s.commit()
        src, correction = Path(video.stored_path), (video.extraction or {}).get("pending_fps")
        stem = src.with_name(src.stem + "_analysis")
        if correction:  # nome novo: a cópia atual só é substituída se a nova estiver certa
            stem = src.with_name(f"{src.stem}_analysis_r{len((video.extraction or {}).get('fps_history', [])) + 1}")
    last = [0.0]

    def progress(fraction: float) -> None:
        now = time.monotonic()
        if now - last[0] >= PROGRESS_EVERY_S:
            last[0] = now
            _set(video_id, extraction_progress=round(min(fraction, 0.99), 3))

    started = time.monotonic()
    try:
        if not src.exists():
            raise ValueError("o ficheiro original não existe")
        if correction:
            analysis, info = sec_format.extract(src, stem, fps=correction["fps"], progress=progress,
                                                fps_source=f"{correction['label']}, por {correction['actor']}")
        else:
            analysis, info = sec_format.extract(src, stem, progress=progress)
    except Exception as exc:  # noqa: BLE001 - a falha fica no vídeo e na auditoria; o original fica guardado
        message = str(exc) or exc.__class__.__name__
        log.warning("Extração do vídeo %s falhou: %s", video_id, message)
        _fail(video_id, message, correction)
        return
    with Session(get_engine(), expire_on_commit=False) as s:
        video = s.get(Video, video_id)
        if video is None:  # eliminado entretanto
            for f in stem.parent.glob(stem.name + "*"):
                f.unlink(missing_ok=True)
            return
        if correction:
            from .fps_fix import apply_fps_correction  # import local (fps_fix usa esta fila)

            apply_fps_correction(s, video, analysis, info, correction)
            return
        _finish(s, video, analysis, info, round(time.monotonic() - started, 1))


def _finish(s: Session, video: Video, analysis: Path, info: dict[str, Any], seconds: float) -> None:
    from .clock_auto import request_auto_clock
    from .transcode import request_playback_copy

    old = video.analysis_path
    if old and old != str(analysis):
        Path(old).unlink(missing_ok=True)
    fps = info["fps"]
    video.analysis_path, video.analysis_sha256, video.extraction = str(analysis), info["analysis_sha256"], info
    video.fps, video.frame_count = round(fps, 3), info["frames"]
    video.duration_s = round(info["frames"] / fps, 3)
    video.width, video.height, video.codec = info["width"], info["height"], "h264"
    video.extraction_status, video.extraction_progress, video.extraction_error = READY, 1.0, ""
    s.add(video)
    s.commit()
    log_event(s, "video.extracted", case_id=video.case_id, actor=ACTOR,
              details={"video_id": video.id, "filename": video.filename, "original_sha256": video.sha256,
                       "seconds": seconds, **{k: v for k, v in info.items() if k != "first_blocks"},
                       "note": "o original não foi alterado; a análise usa o vídeo extraído sem recodificação"})
    request_playback_copy(s, video)
    request_auto_clock(s, video)


def _fail(video_id: int, message: str, correction: dict[str, Any] | None) -> None:
    with Session(get_engine()) as s:
        video = s.get(Video, video_id)
        if video is None:
            return
        if correction and video.analysis_path and Path(video.analysis_path).exists():
            # a correção falhou mas a cópia anterior continua válida: o vídeo fica como estava
            video.extraction = {k: v for k, v in (video.extraction or {}).items() if k != "pending_fps"}
            video.extraction_status, video.extraction_progress = READY, 1.0
            video.extraction_error = f"a correção do fps falhou (mantém-se {video.fps:g} fps): {message}"[:500]
            action = "video.fps_correction_failed"
        else:
            video.extraction_status, video.extraction_error = FAILED, message[:500]
            action = "video.extraction_failed"
        s.add(video)
        s.commit()
        log_event(s, action, case_id=video.case_id, actor=ACTOR,
                  details={"video_id": video.id, "filename": video.filename, "error": message[:500],
                           "requested_fps": (correction or {}).get("fps"),
                           "note": "o original continua guardado e intacto; pode tentar de novo"})
