"""Cópia de reprodução H.264/MP4 para o navegador (o original nunca é alterado e é o que é analisado).

Os browsers só reproduzem alguns codecs/contentores (H.264 8 bits 4:2:0, VP8/VP9, AV1 em MP4/WebM). Ficheiros de
DVR (H.265/HEVC, .dav, .avi com MPEG-4, H.264 10 bits ou 4:2:2, ...) dão "nenhum vídeo com formato e tipo MIME
suportado". Para esses é criada uma cópia H.264 só para visualização, numa fila (uma conversão de cada vez) com
estado e progresso guardados no vídeo. As conversões pendentes são retomadas no arranque do servidor.
"""

from __future__ import annotations

import json
import logging
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from sqlmodel import Session, select

from ..db import get_engine
from ..models import Video
from .audit import log_event

log = logging.getLogger(__name__)

BROWSER_CODECS = {"avc1", "h264", "vp80", "vp90", "av01"}  # FourCC (OpenCV), quando não há ffprobe
BROWSER_STREAM_CODECS = {"h264", "vp8", "vp9", "av1"}  # codec_name do ffprobe
BROWSER_PIX_FMTS = {"yuv420p", "yuvj420p"}
BROWSER_CONTAINERS = {".mp4", ".webm", ".m4v", ".mov"}

# Estados de Video.playback_status
NOT_NEEDED, PENDING, RUNNING, READY, FAILED, UNAVAILABLE = (
    "not_needed", "pending", "running", "ready", "failed", "unavailable")

_queue: queue.Queue[int] = queue.Queue()
_queued: set[int] = set()
_lock = threading.Lock()
_worker: threading.Thread | None = None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_stream(path: str | Path) -> dict | None:
    """codec_name, profile e pix_fmt do primeiro stream de vídeo (ffprobe), ou None se não for possível."""
    if shutil.which("ffprobe") is None:
        return None
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=codec_name,profile,pix_fmt", "-of", "json", str(path)]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, timeout=60).stdout
        streams = json.loads(out or b"{}").get("streams") or []
        return streams[0] if streams else None
    except Exception:  # noqa: BLE001 - ffprobe falhou: decide-se pelo FourCC
        return None


def needs_playback_copy(video: Video) -> tuple[bool, str]:
    ext = Path(video.filename).suffix.lower()
    if ext not in BROWSER_CONTAINERS:
        return True, f"contentor {ext or 'desconhecido'} não suportado pelos browsers"
    info = probe_stream(video.stored_path)
    if info is None:
        ok = video.codec.lower() in BROWSER_CODECS
        return (not ok, "" if ok else f"codec {video.codec or 'desconhecido'} não suportado pelos browsers")
    codec, pix = (info.get("codec_name") or "").lower(), (info.get("pix_fmt") or "").lower()
    if codec not in BROWSER_STREAM_CODECS:
        return True, f"codec {codec or 'desconhecido'} não suportado pelos browsers"
    if pix and pix not in BROWSER_PIX_FMTS:
        return True, f"formato de cor {pix} ({info.get('profile') or codec}) não suportado pelos browsers"
    return False, ""


def request_playback_copy(s: Session, video: Video, *, force: bool = False, actor: str = "sistema") -> str:
    """Decide se o vídeo precisa de cópia de reprodução e, se sim, põe-na na fila. Devolve o novo estado."""
    if video.playback_status in (PENDING, RUNNING) and video.id in _queued:
        return video.playback_status
    if not ffmpeg_available():
        video.playback_status, video.playback_error = UNAVAILABLE, "ffmpeg não está instalado no servidor."
    else:
        need, reason = (True, "pedido manual") if force else needs_playback_copy(video)
        if need:
            video.playback_status, video.playback_error, video.playback_progress = PENDING, "", 0.0
        else:
            video.playback_status, video.playback_error = NOT_NEEDED, ""
    s.add(video)
    s.commit()
    if video.playback_status == PENDING:
        log_event(s, "video.playback_copy_requested", case_id=video.case_id, actor=actor,
                  details={"video_id": video.id, "reason": reason, "forced": force,
                           "note": "cópia H.264 apenas para visualização; o original é o analisado"})
        _enqueue(video.id)
    return video.playback_status


def resume_pending_copies() -> None:
    """No arranque: retoma conversões interrompidas e avalia vídeos importados por versões anteriores."""
    if not ffmpeg_available():
        return
    with Session(get_engine(), expire_on_commit=False) as s:
        for video in s.exec(select(Video).where(Video.playback_status.in_(["", PENDING, RUNNING]))):
            if video.playback_status == "" and video.playback_path and Path(video.playback_path).exists():
                video.playback_status = READY
                s.add(video)
                s.commit()
                continue
            if not Path(video.stored_path).exists():
                continue
            if video.playback_status in (PENDING, RUNNING):
                video.playback_status = PENDING
                s.add(video)
                s.commit()
                _enqueue(video.id)
            else:
                request_playback_copy(s, video)


def _enqueue(video_id: int) -> None:
    global _worker
    with _lock:
        if video_id in _queued:
            return
        _queued.add(video_id)
        _queue.put(video_id)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, daemon=True, name="faceai-transcode")
            _worker.start()


def _work() -> None:
    while True:
        try:
            video_id = _queue.get(timeout=60)
        except queue.Empty:
            with _lock:
                if _queue.empty():
                    global _worker
                    _worker = None
                    return
            continue
        try:
            _transcode(video_id)
        except Exception:  # noqa: BLE001
            log.exception("Falha inesperada na conversão do vídeo %s", video_id)
        finally:
            with _lock:
                _queued.discard(video_id)


def _set(video_id: int, **fields) -> Video | None:
    with Session(get_engine(), expire_on_commit=False) as s:
        video = s.get(Video, video_id)
        if video is None:
            return None
        for k, v in fields.items():
            setattr(video, k, v)
        s.add(video)
        s.commit()
        return video


def _transcode(video_id: int) -> None:
    video = _set(video_id, playback_status=RUNNING, playback_progress=0.0)
    if video is None:
        return
    src = Path(video.stored_path)
    dst = src.with_name(src.stem + "_playback.mp4")
    tmp = src.with_name(src.stem + "_playback.part.mp4")
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-i", str(src),
        "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-movflags", "+faststart", str(tmp),
    ]
    duration = video.duration_s or 0.0
    started, last_update = time.monotonic(), 0.0
    errlog = tempfile.TemporaryFile(mode="w+")  # ficheiros de DVR danificados podem gerar muitos erros
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errlog, text=True)
        for line in proc.stdout:  # type: ignore[union-attr]
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and duration > 0 and value.isdigit():
                now = time.monotonic()
                if now - last_update >= 3:
                    last_update = now
                    _set(video_id, playback_progress=round(min(0.99, int(value) / 1e6 / duration), 3))
            if time.monotonic() - started > 6 * 3600:
                proc.kill()
                raise TimeoutError("a conversão excedeu 6 h")
        if proc.wait() != 0:
            errlog.seek(0)
            lines = errlog.read().strip().splitlines()
            raise RuntimeError(lines[-1] if lines else f"o ffmpeg terminou com o código {proc.returncode}")
        tmp.replace(dst)
    except Exception as exc:  # noqa: BLE001
        errlog.close()
        tmp.unlink(missing_ok=True)
        log.warning("Falha ao criar cópia de reprodução do vídeo %s: %s", video_id, exc)
        video = _set(video_id, playback_status=FAILED, playback_error=str(exc)[:500])
        if video is not None:
            with Session(get_engine()) as s:
                log_event(s, "video.playback_copy_failed", case_id=video.case_id, actor="sistema",
                          details={"video_id": video_id, "error": str(exc)[:500]})
        return
    errlog.close()
    video = _set(video_id, playback_status=READY, playback_path=str(dst), playback_progress=1.0, playback_error="")
    if video is not None:
        with Session(get_engine()) as s:
            log_event(s, "video.playback_copy_ready", case_id=video.case_id, actor="sistema",
                      details={"video_id": video_id, "size_bytes": dst.stat().st_size,
                               "seconds": round(time.monotonic() - started, 1),
                               "note": "cópia H.264 apenas para visualização; o original é o analisado"})
