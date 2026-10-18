"""Cópia de reprodução H.264/MP4 para o navegador (o original nunca é alterado e é o que é analisado).

Os browsers só reproduzem alguns codecs/contentores (H.264 8 bits 4:2:0, VP8/VP9, AV1 em MP4/WebM). Ficheiros de
DVR (H.265/HEVC, .dav, .avi com MPEG-4, H.264 10 bits ou 4:2:2, ...) dão "nenhum vídeo com formato e tipo MIME
suportado". Para esses é criada uma cópia só para visualização (H.264/MP4; ou VP9/WebM para browsers sem
H.264, como alguns Firefox/Chromium em Linux), numa fila (uma conversão de cada vez) com
estado e progresso guardados no vídeo. As conversões pendentes são retomadas no arranque do servidor.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from sqlmodel import Session, select

from ..config import settings
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
_running: dict[int, subprocess.Popen] = {}  # conversões em curso (para poderem ser canceladas)
_cancelled: set[int] = set()
_requeue: set[int] = set()  # canceladas para voltar à fila (o vídeo de origem mudou)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


# Contentores (format_name do ffprobe) que os browsers abrem
BROWSER_FORMATS = {"mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm"}
FORMATS = {"mp4": "video/mp4", "webm": "video/webm"}  # formatos da cópia de reprodução


def probe_stream(path: str | Path) -> dict | None:
    """format_name e codec_name/profile/pix_fmt/width/height do 1.º stream de vídeo (ffprobe), ou None."""
    if shutil.which("ffprobe") is None:
        return None
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=codec_name,profile,pix_fmt,width,height:format=format_name", "-of", "json", str(path)]
    try:
        out = json.loads(subprocess.run(cmd, check=True, capture_output=True, timeout=60).stdout or b"{}")
        streams = out.get("streams") or []
        if not streams:
            return None
        return {**streams[0], "format_name": (out.get("format") or {}).get("format_name", "")}
    except Exception:  # noqa: BLE001 - ffprobe falhou: decide-se pelo FourCC
        return None


def needs_playback_copy(video: Video) -> tuple[bool, str]:
    ext = Path(video.analysis_path or video.filename).suffix.lower()  # .sec: conta o contentor do vídeo extraído
    if ext not in BROWSER_CONTAINERS:
        return True, f"contentor {ext or 'desconhecido'} não suportado pelos browsers"
    info = probe_stream(video.source_path)
    if info is None:
        ok = video.codec.lower() in BROWSER_CODECS
        return (not ok, "" if ok else f"codec {video.codec or 'desconhecido'} não suportado pelos browsers")
    codec, pix = (info.get("codec_name") or "").lower(), (info.get("pix_fmt") or "").lower()
    fmt = info.get("format_name") or ""
    if fmt and fmt not in BROWSER_FORMATS:
        # ex.: H.264 "cru" exportado pelo DVR com extensão .mp4
        return True, f"o ficheiro {ext} é na verdade {fmt}, que os browsers não abrem"
    if codec not in BROWSER_STREAM_CODECS:
        return True, f"codec {codec or 'desconhecido'} não suportado pelos browsers"
    if pix and pix not in BROWSER_PIX_FMTS:
        return True, f"formato de cor {pix} ({info.get('profile') or codec}) não suportado pelos browsers"
    return False, ""


def request_playback_copy(s: Session, video: Video, *, force: bool = False, actor: str = "sistema",
                          fmt: str = "mp4") -> str:
    """Decide se o vídeo precisa de cópia de reprodução e, se sim, põe-na na fila. Devolve o novo estado.

    fmt: "mp4" (H.264, o mais compatível) ou "webm" (VP9, para browsers sem H.264, ex.: alguns em Linux).
    """
    if fmt not in FORMATS:
        raise ValueError(f"Formato de reprodução desconhecido: {fmt}")
    if video.playback_status in (PENDING, RUNNING) and video.id in _queued:
        return video.playback_status
    if video.extraction_status and video.extraction_status != "ready":
        return video.playback_status  # .sec ainda a extrair: a cópia é pedida no fim da extração
    if not ffmpeg_available():
        video.playback_status, video.playback_error = UNAVAILABLE, "ffmpeg não está instalado no servidor."
    else:
        need, reason = (True, "pedido manual") if force else needs_playback_copy(video)
        if need:
            video.playback_status, video.playback_error, video.playback_progress = PENDING, "", 0.0
            video.playback_format = fmt
        else:
            video.playback_status, video.playback_error = NOT_NEEDED, ""
    s.add(video)
    s.commit()
    if video.playback_status == PENDING:
        log_event(s, "video.playback_copy_requested", case_id=video.case_id, actor=actor,
                  details={"video_id": video.id, "reason": reason, "forced": force, "format": fmt,
                           "note": "cópia apenas para visualização; o original é o analisado"})
        _enqueue(video.id)
    return video.playback_status


def request_case_copies(s: Session, case_id: int, *, fmt: str = "mp4", actor: str = "sistema") -> list[Video]:
    """Põe na fila a cópia de reprodução de todos os vídeos do caso (ex.: o browser não reproduziu um deles, e os
    restantes vídeos do mesmo DVR terão o mesmo formato). Salta os que já têm a cópia nesse formato ou estão na fila."""
    videos = list(s.exec(select(Video).where(Video.case_id == case_id).order_by(Video.id)))
    for video in videos:
        if video.playback_status in (PENDING, RUNNING, UNAVAILABLE):
            continue
        if video.playback_status == READY and (video.playback_format or "mp4") == fmt:
            continue
        if not Path(video.source_path).exists():
            continue
        request_playback_copy(s, video, force=True, actor=actor, fmt=fmt)
    return videos


def cancel_copies(video_ids: list[int]) -> None:
    """Cancela as conversões (na fila ou em curso) destes vídeos, ex.: antes de eliminar o caso."""
    with _lock:
        for vid in video_ids:
            if vid not in _queued and vid not in _running:
                continue  # nada a cancelar (e o id pode vir a ser reutilizado por um vídeo novo)
            _cancelled.add(vid)
            proc = _running.get(vid)
            if proc is not None and proc.poll() is None:
                proc.kill()


def restart_copy(s: Session, video: Video, *, actor: str = "sistema") -> str:
    """Refaz a cópia de reprodução porque o vídeo de origem mudou (ex.: fps corrigido). Uma conversão na fila ou em
    curso é cancelada e volta à fila; uma cópia pronta é apagada e pedida de novo (no mesmo formato)."""
    with _lock:
        busy = video.id in _queued or video.id in _running
        if busy:
            _cancelled.add(video.id)
            _requeue.add(video.id)
            proc = _running.get(video.id)
            if proc is not None and proc.poll() is None:
                proc.kill()
    old = video.playback_path
    if old:
        Path(old).unlink(missing_ok=True)
    video.playback_path, video.playback_progress, video.playback_error = None, 0.0, ""
    if busy:
        video.playback_status = PENDING
        s.add(video)
        s.commit()
        return PENDING
    video.playback_status = ""
    return request_playback_copy(s, video, force=bool(old), actor=actor, fmt=video.playback_format or "mp4")


def resume_pending_copies() -> None:
    """No arranque (em segundo plano): retoma conversões interrompidas e avalia vídeos de versões anteriores.

    Uma conversão que já não pode correr (ffmpeg removido, original em falta) fica "indisponível"/"falhou" com o
    motivo, em vez de ficar "pendente" para sempre (com a interface a perguntar o estado de poucos em poucos s)."""
    has_ffmpeg = ffmpeg_available()
    with Session(get_engine(), expire_on_commit=False) as s:
        for video in s.exec(select(Video).where(Video.playback_status.in_(["", PENDING, RUNNING]))):
            if video.playback_status == "" and video.playback_path and Path(video.playback_path).exists():
                video.playback_status = READY
                s.add(video)
                s.commit()
                continue
            if not has_ffmpeg:
                if video.playback_status in (PENDING, RUNNING):
                    video.playback_status, video.playback_error = UNAVAILABLE, "ffmpeg não está instalado no servidor."
                    s.add(video)
                    s.commit()
                continue
            if not Path(video.source_path).exists():
                if video.playback_status in (PENDING, RUNNING):
                    video.playback_status, video.playback_error = FAILED, "O ficheiro original não foi encontrado."
                    s.add(video)
                    s.commit()
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
            with _lock:
                skip = video_id in _cancelled
            if not skip:
                _transcode(video_id)
        except Exception:  # noqa: BLE001
            log.exception("Falha inesperada na conversão do vídeo %s", video_id)
        finally:
            with _lock:
                _cancelled.discard(video_id)
                _running.pop(video_id, None)
                if video_id in _requeue:
                    _requeue.discard(video_id)
                    _queue.put(video_id)  # continua em _queued
                else:
                    _queued.discard(video_id)


def _lower_priority() -> None:  # pragma: no cover - corre no processo filho
    try:
        os.nice(10)
    except OSError:
        pass


def _progress_seconds(key: str, value: str) -> float | None:
    """Tempo já convertido a partir das linhas de `ffmpeg -progress` (as chaves variam entre versões)."""
    try:
        if key in ("out_time_us", "out_time_ms") and value.lstrip("-").isdigit():
            return int(value) / 1e6  # out_time_ms também está em microssegundos (histórico do ffmpeg)
        if key == "out_time" and value.count(":") == 2:
            h, m, sec = value.split(":")
            return int(h) * 3600 + int(m) * 60 + float(sec)
    except ValueError:
        return None
    return None


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
    fmt = video.playback_format if video.playback_format in FORMATS else "mp4"
    src = Path(video.source_path)
    dst = src.with_name(f"{src.stem}_playback.{fmt}")
    tmp = src.with_name(f"{src.stem}_playback.part.{fmt}")
    if fmt == "webm":  # VP9 em tempo real: mais lento que H.264 mas reproduz em qualquer browser moderno
        codec = ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
                 "-crf", "33", "-b:v", "0"]
    else:
        codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart"]
    threads = str(settings.cpu_threads or max(1, (os.cpu_count() or 2) // 2))
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
        "-threads", threads, "-i", str(src), "-map", "0:v:0", "-an", *codec, "-threads", threads,
        "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", str(tmp),
    ]
    duration = video.duration_s or 0.0
    started, last_update = time.monotonic(), 0.0
    errlog = tempfile.TemporaryFile(mode="w+")  # ficheiros de DVR danificados podem gerar muitos erros
    try:
        # Prioridade baixa e metade dos núcleos: a conversão (só para visualização) não deve atrasar as análises
        # nem deixar a interface sem resposta.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errlog, text=True,
                                preexec_fn=_lower_priority if os.name == "posix" else None)
        with _lock:
            _running[video_id] = proc
            if video_id in _cancelled:  # cancelada entretanto (ex.: o caso foi eliminado)
                proc.kill()
        for line in proc.stdout:  # type: ignore[union-attr]
            key, _, value = line.strip().partition("=")
            seconds = _progress_seconds(key, value)
            if seconds is not None and duration > 0:
                now = time.monotonic()
                if now - last_update >= 2:
                    last_update = now
                    _set(video_id, playback_progress=round(min(0.99, max(0.0, seconds / duration)), 3))
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
        with _lock:
            if video_id in _cancelled:
                return
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
                      details={"video_id": video_id, "format": fmt, "size_bytes": dst.stat().st_size,
                               "seconds": round(time.monotonic() - started, 1),
                               "note": "cópia apenas para visualização; o original é o analisado"})


def start_resume_pending_copies() -> None:
    """O ffprobe dos vídeos antigos pode demorar: corre numa thread para não atrasar o arranque do servidor."""
    threading.Thread(target=resume_pending_copies, name="faceai-transcode-resume", daemon=True).start()
