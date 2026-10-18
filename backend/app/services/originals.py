"""Cópias de análise sem perdas e remoção dos originais convertidos (poupar espaço no servidor).

A evidência mestre é o suporte/exportação do DVR, guardado fora do servidor. Depois de um vídeo ser convertido, o
servidor pode ficar só com a cópia de trabalho — desde que seja **sem perdas** e esteja verificada:

- .sec: a cópia tem as cargas H.264 dos blocos, sem recodificação. A verificação recalcula o SHA-256 da sequência
  H.264 a partir da cópia (MP4 → Annex B) e compara-o com o calculado ao ler o original.
- outros formatos que o browser não abre (.dav, .avi com HEVC/MPEG-4, …): a cópia é um remux -c copy para MKV
  (os mesmos pacotes de vídeo, byte a byte). A verificação compara o hash dos pacotes (ffmpeg streamhash) da
  cópia com o do original. A cópia de reprodução (recodificada, com perdas) nunca é a analisada.

Só então o original é apagado, depois de o seu SHA-256 ser confirmado de novo. Fica o SHA-256 (e o tamanho e o
nome) para relacionar a cópia com o original; reenviar o mesmo ficheiro volta a anexá-lo ao vídeo. Qualquer
falha na verificação mantém o original, com o motivo na auditoria.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from sqlmodel import Session

from .. import config
from ..models import Video, utcnow
from . import sec_format
from .audit import log_event
from .hashing import file_sha256

log = logging.getLogger(__name__)

REMUX_METHOD = "remux-copy-1"
CHUNK = 8 * 1024 * 1024


def enabled() -> bool:
    return config.settings.remove_originals  # lida na hora (a configuração pode ser recarregada)


def _ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def stream_hash(path: str | Path) -> tuple[str, int]:
    """(SHA-256 dos pacotes do 1.º stream de vídeo, n.º de pacotes)."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy",
                          "-f", "streamhash", "-hash", "sha256", "-"], check=True, capture_output=True,
                         timeout=7200).stdout.decode()
    digest = out.strip().rsplit("=", 1)[-1]
    packets = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                              "stream=nb_read_packets", "-of", "csv=p=0", str(path)], check=True,
                             capture_output=True, timeout=7200).stdout.decode().strip().split(",")[0]
    return digest, int(packets or 0)


def annexb_sha256(path: str | Path) -> tuple[str, int]:
    """SHA-256 da sequência H.264 (Annex B) contida na cópia de um .sec, e o seu tamanho."""
    path = Path(path)
    h, size = hashlib.sha256(), 0
    if path.suffix == ".h264":  # sem ffmpeg: a cópia é a própria sequência
        with path.open("rb") as fh:
            while chunk := fh.read(CHUNK):
                h.update(chunk)
                size += len(chunk)
        return h.hexdigest(), size
    proc = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy",
                             "-bsf:v", "h264_mp4toannexb", "-f", "h264", "-"], stdout=subprocess.PIPE)
    try:
        while chunk := proc.stdout.read(CHUNK):  # type: ignore[union-attr]
            h.update(chunk)
            size += len(chunk)
    finally:
        code = proc.wait()
    if code != 0:
        raise ValueError("não foi possível ler a sequência H.264 da cópia de análise")
    return h.hexdigest(), size


def remux_for_analysis(src: Path, stem: Path) -> tuple[Path, dict[str, Any]]:
    """Cópia de análise sem perdas (-c copy para MKV) de um vídeo que o browser não abre."""
    if not _ffmpeg():
        raise ValueError("ffmpeg indisponível: o original fica como cópia analisada")
    original_hash, original_packets = stream_hash(src)
    out, part = stem.with_name(stem.name + ".mkv"), stem.with_name(stem.name + ".part.mkv")
    sec_format._check_space(stem.parent, src.stat().st_size + sec_format.SPACE_MARGIN)
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-map", "0:v:0", "-c", "copy",
                        str(part)], check=True, capture_output=True, timeout=7200)
        copy_hash, copy_packets = stream_hash(part)
    except subprocess.CalledProcessError as exc:
        part.unlink(missing_ok=True)
        raise ValueError(f"remux falhou: {(exc.stderr or b'').decode('utf-8', 'replace')[-300:]}") from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if copy_hash != original_hash or copy_packets != original_packets:
        part.unlink(missing_ok=True)
        raise ValueError("a cópia por remux não tem os mesmos pacotes de vídeo que o original")
    part.replace(out)
    return out, {"method": REMUX_METHOD, "container": "mkv (-c copy, sem recodificação)",
                 "packets": copy_packets, "original_streamhash": original_hash, "streamhash": copy_hash,
                 "analysis_sha256": file_sha256(out)}


def retime_sec_copy(video: Video, stem: Path, fps: float, fps_source: str) -> tuple[Path, dict[str, Any]]:
    """Refaz a cópia de um .sec com outro fps a partir da própria cópia (não precisa do original): a sequência
    H.264 sai da cópia e volta a entrar num MP4 com o novo ritmo, sem recodificação; tem de ter o mesmo SHA-256."""
    src = Path(video.analysis_path or "")
    x = {k: v for k, v in (video.extraction or {}).items() if k not in ("pending_fps", "suggested_fps")}
    if src.suffix == ".h264" or not _ffmpeg():  # sequência crua: o fps é só um metadado
        return src, {**x, "fps": fps, "fps_source": fps_source, "fps_assumed": False}
    out, part = stem.with_name(stem.name + ".mp4"), stem.with_name(stem.name + ".part.mp4")
    sec_format._check_space(stem.parent, src.stat().st_size + sec_format.SPACE_MARGIN)
    reader = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-i", str(src), "-map", "0:v:0", "-c", "copy",
                               "-bsf:v", "h264_mp4toannexb", "-f", "h264", "-"], stdout=subprocess.PIPE)
    writer = subprocess.Popen(["ffmpeg", "-nostdin", "-y", "-v", "error", "-fflags", "+genpts", "-r", f"{fps:g}",
                               "-f", "h264", "-i", "pipe:0", "-map", "0:v:0", "-c", "copy", "-movflags",
                               "+faststart", str(part)], stdin=subprocess.PIPE)
    h = hashlib.sha256()
    try:
        while chunk := reader.stdout.read(CHUNK):  # type: ignore[union-attr]
            h.update(chunk)
            writer.stdin.write(chunk)  # type: ignore[union-attr]
        writer.stdin.close()  # type: ignore[union-attr]
        codes = reader.wait(), writer.wait(timeout=7200)
    except BaseException:
        reader.kill()
        writer.kill()
        part.unlink(missing_ok=True)
        raise
    if any(codes):
        part.unlink(missing_ok=True)
        raise ValueError("ffmpeg falhou ao refazer a cópia com o novo fps")
    if h.hexdigest() != x.get("h264_sha256"):
        part.unlink(missing_ok=True)
        raise ValueError("a sequência H.264 da cópia não coincide com a extraída do original")
    part.replace(out)
    return out, {**x, "fps": fps, "fps_source": fps_source, "fps_assumed": False,
                 "container": "mp4 (-c copy, sem recodificação)", "analysis_sha256": file_sha256(out)}


def verify_copy(video: Video) -> tuple[bool, str]:
    """A cópia de análise contém exatamente o vídeo do original? (antes de o apagar)"""
    x = video.extraction or {}
    copy = Path(video.analysis_path or "")
    if not video.analysis_path or not copy.exists():
        return False, "sem cópia de análise"
    if video.analysis_sha256 and file_sha256(copy) != video.analysis_sha256:
        return False, "a cópia de análise não corresponde ao SHA-256 registado"
    try:
        if x.get("method") == REMUX_METHOD:
            digest, packets = stream_hash(copy)
            if digest != x.get("original_streamhash") or packets != x.get("packets"):
                return False, "os pacotes de vídeo da cópia diferem dos do original"
            return True, digest
        if x.get("method") == sec_format.METHOD:
            digest, size = annexb_sha256(copy)
            if digest != x.get("h264_sha256") or size != x.get("h264_bytes"):
                return False, "a sequência H.264 da cópia difere da extraída do original"
            return True, stream_hash(copy)[0] if _ffmpeg() and copy.suffix != ".h264" else digest
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return False, f"não foi possível verificar a cópia: {exc}"
    return False, "método de cópia desconhecido"


def remove_original(s: Session, video: Video, *, actor: str = "sistema") -> bool:
    """Apaga o original se a cópia estiver verificada e o original intacto. Devolve True se o apagou."""
    original = Path(video.stored_path)
    if video.original_removed_at is not None or not original.exists():
        return False
    ok, detail = verify_copy(video)
    reason = "" if ok else detail
    if ok and file_sha256(original) != video.sha256:
        ok, reason = False, "o SHA-256 do original não coincide com o da importação"
    if not ok:
        log_event(s, "video.original_kept", case_id=video.case_id, actor=actor,
                  details={"video_id": video.id, "filename": video.filename, "reason": reason})
        return False
    size = original.stat().st_size
    original.unlink()
    video.original_removed_at, video.analysis_streamhash = utcnow(), detail
    s.add(video)
    s.commit()
    log_event(s, "video.original_removed", case_id=video.case_id, actor=actor,
              details={"video_id": video.id, "filename": video.filename, "original_sha256": video.sha256,
                       "original_size_bytes": size, "analysis_sha256": video.analysis_sha256,
                       "video_streamhash": detail, "method": (video.extraction or {}).get("method"),
                       "note": "o original deve estar preservado no suporte de origem; o SHA-256 permite confirmar "
                               "a correspondência. Reenviar o mesmo ficheiro volta a anexá-lo."})
    return True


def reattach(s: Session, video: Video, path: Path, *, actor: str) -> Video:
    """O mesmo ficheiro (SHA-256 igual) foi reenviado: volta a ser o original do vídeo."""
    dest = Path(video.stored_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    path.replace(dest)
    video.original_removed_at = None
    s.add(video)
    s.commit()
    s.refresh(video)
    log_event(s, "video.original_reattached", case_id=video.case_id, actor=actor,
              details={"video_id": video.id, "filename": video.filename, "sha256": video.sha256})
    return video
