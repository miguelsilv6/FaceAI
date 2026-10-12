"""Cópia de reprodução H.264/MP4 para o navegador (o original nunca é alterado e é o que é analisado)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path

from sqlmodel import Session

from ..db import get_engine
from ..models import Video

log = logging.getLogger(__name__)

BROWSER_CODECS = {"avc1", "h264", "vp80", "vp90", "av01"}
BROWSER_CONTAINERS = {".mp4", ".webm", ".m4v", ".mov"}


def needs_playback_copy(video: Video) -> bool:
    return not (
        video.codec.lower() in BROWSER_CODECS and Path(video.filename).suffix.lower() in BROWSER_CONTAINERS
    )


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _transcode(video_id: int, src: str, dst: Path) -> None:
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", src,
        "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=6 * 3600)
    except Exception as exc:
        log.warning("Falha ao criar cópia de reprodução do vídeo %s: %s", video_id, exc)
        dst.unlink(missing_ok=True)
        return
    with Session(get_engine()) as s:
        video = s.get(Video, video_id)
        if video is not None:
            video.playback_path = str(dst)
            s.add(video)
            s.commit()


def schedule_playback_copy(video: Video) -> bool:
    """Lança a conversão em segundo plano, se necessária e possível."""
    if not needs_playback_copy(video) or not ffmpeg_available():
        return False
    src = Path(video.stored_path)
    dst = src.with_name(src.stem + "_playback.mp4")
    threading.Thread(target=_transcode, args=(video.id, str(src), dst), daemon=True,
                     name=f"transcode-{video.id}").start()
    return True
