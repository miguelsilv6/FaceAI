"""Leitura automática da hora real dos vídeos (OCR da data/hora impressa) logo após a importação.

Corre numa thread própria, um vídeo de cada vez e com prioridade baixa, para não atrasar as análises. Só acerta o
relógio se a calibração for coerente (o mesmo critério do botão "Ler por OCR"); caso contrário deixa o vídeo sem
hora e regista o motivo, para o analista acertar à mão. Nunca substitui uma hora já acertada.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path

from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine
from ..models import Video
from . import ocr
from .audit import log_event

log = logging.getLogger(__name__)

PENDING, RUNNING, DONE, FAILED, UNAVAILABLE = "pending", "running", "done", "failed", "unavailable"
ACTOR = "sistema (OCR automático)"
SAMPLES = 12

_queue: queue.Queue[int] = queue.Queue()
_queued: set[int] = set()
_lock = threading.Lock()
_worker: threading.Thread | None = None


def request_auto_clock(s: Session, video: Video) -> None:
    """Põe o vídeo na fila da leitura automática da hora (se ativa e se o vídeo ainda não tiver hora)."""
    if not settings.auto_clock_ocr or video.dvr_start:
        return
    video.clock_auto_status, video.clock_auto_message = PENDING, ""
    s.add(video)
    s.commit()
    _enqueue(video.id)


def resume_pending_auto_clock() -> None:
    """No arranque: retoma as leituras que ficaram por fazer. Com o OCR automático desligado entretanto, as que
    estavam pendentes passam a "indisponível" (senão ficavam "pendentes" para sempre e a interface a atualizar)."""
    with Session(get_engine()) as s:
        for video in s.exec(select(Video).where(Video.clock_auto_status.in_([PENDING, RUNNING]))):
            if settings.auto_clock_ocr:
                _enqueue(video.id)
            else:
                video.clock_auto_status = UNAVAILABLE
                video.clock_auto_message = "Leitura automática da hora desligada (FACEAI_AUTO_CLOCK_OCR=0)."
                s.add(video)
        s.commit()


def process(video_id: int) -> None:
    """Lê a hora do vídeo e acerta o relógio. Público para os testes (corre de forma síncrona)."""
    with Session(get_engine()) as s:
        v = s.get(Video, video_id)
        if v is None:
            return
        if v.dvr_start or not Path(v.stored_path).exists():
            if v.clock_auto_status in (PENDING, RUNNING):
                v.clock_auto_status = ""
                s.add(v)
                s.commit()
            return
        v.clock_auto_status = RUNNING
        s.add(v)
        s.commit()
        path, fps, duration = v.stored_path, v.fps, v.duration_s
    status, message, cal = FAILED, "", None
    try:
        cal = ocr.calibrate(path, fps, duration, roi=None, samples=SAMPLES, date_order=settings.clock_date_order)
        if "error" in cal:
            message = cal["error"]
        else:
            status = DONE
    except ocr.OcrUnavailable as exc:
        status, message = UNAVAILABLE, str(exc)
    except ValueError as exc:
        message = str(exc)
    except Exception as exc:  # noqa: BLE001 - um vídeo estranho não pode parar a fila
        log.exception("Falha na leitura automática da hora do vídeo %s", video_id)
        message = f"Erro na leitura automática: {exc}"
    with Session(get_engine()) as s:
        v = s.get(Video, video_id)
        if v is None:
            return
        if v.dvr_start:  # acertada à mão entretanto: não mexer
            v.clock_auto_status = ""
            s.add(v)
            s.commit()
            return
        v.clock_auto_status, v.clock_auto_message = status, message
        if status == DONE and cal is not None:
            v.dvr_start, v.dvr_rate, v.clock_offset_s = cal["dvr_start"], cal["rate"], 0.0
            v.time_source = "ocr"
            v.time_note = "Lida automaticamente por OCR na importação: confirme."
            v.time_calibration = cal
        s.add(v)
        s.commit()
        if status == DONE:
            log_event(s, "video.clock_set", case_id=v.case_id, actor=ACTOR,
                      details={"video_id": v.id, "time_source": "ocr", "dvr_start": v.dvr_start,
                               "dvr_rate": v.dvr_rate, "automatic": True, "calibration": cal})
        elif status == FAILED:
            log_event(s, "video.clock_ocr_failed", case_id=v.case_id, actor=ACTOR,
                      details={"video_id": v.id, "error": message, "automatic": True,
                               "samples": (cal or {}).get("samples", [])})


def _enqueue(video_id: int) -> None:
    global _worker
    with _lock:
        if video_id in _queued:
            return
        _queued.add(video_id)
        _queue.put(video_id)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, daemon=True, name="faceai-clock-ocr")
            _worker.start()


def _work() -> None:
    global _worker
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)  # só esta thread (Linux)
    except (AttributeError, OSError):
        pass
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
            process(video_id)
        except Exception:  # noqa: BLE001
            log.exception("Falha inesperada na leitura automática da hora do vídeo %s", video_id)
        finally:
            with _lock:
                _queued.discard(video_id)
