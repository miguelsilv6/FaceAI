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
                             capture_output=True, timeout=7200).stdout.decode()
    first = next((line.strip().split(",")[0] for line in packets.splitlines() if line.strip()), "0")
    return digest, int(first or 0)


def probe_codec(path: str | Path) -> tuple[str, str]:
    """(codec_name, pix_fmt) do 1.º stream de vídeo."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,pix_fmt", "-of", "csv=p=0", str(path)], check=True, capture_output=True,
                         timeout=600).stdout.decode().strip().split("\n")[0].split(",")
    return (out[0] if out else "").lower(), (out[1] if len(out) > 1 else "").lower()


def nal_fingerprint(path: str | Path, codec: str = "h264") -> dict[str, Any]:
    """Impressão (sec_format.NalFingerprint) do vídeo H.264/HEVC de um ficheiro, em qualquer contentor."""
    path = Path(path)
    fp = sec_format.NalFingerprint(codec)
    if path.suffix == ".h264":  # sequência crua (cópia de .sec sem ffmpeg)
        with path.open("rb") as fh:
            while chunk := fh.read(CHUNK):
                fp.update(chunk)
        return fp.result()
    kind = "hevc" if codec in ("hevc", "h265") else "h264"
    proc = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy",
                             "-bsf:v", f"{kind}_mp4toannexb", "-f", kind, "-"], stdout=subprocess.PIPE)
    try:
        while chunk := proc.stdout.read(CHUNK):  # type: ignore[union-attr]
            fp.update(chunk)
    finally:
        code = proc.wait()
    if code != 0:
        raise ValueError("não foi possível ler o vídeo H.264/HEVC do ficheiro")
    return fp.result()


def decoded_hash(path: str | Path) -> str:
    """SHA-256 dos frames descodificados (para codecs sem NAL, quando os pacotes mudam com o contentor)."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "streamhash",
                          "-hash", "sha256", "-"], check=True, capture_output=True, timeout=7200).stdout.decode()
    return out.strip().rsplit("=", 1)[-1]


def remux_worthwhile(src: Path) -> bool:
    """Só vale a pena uma cópia por remux se o resultado (MP4) for reproduzível no browser: o problema é só o
    contentor. Com um codec que o browser não reproduz (HEVC, MPEG-4, 10 bits), o original é a cópia analisada e
    faz-se apenas a cópia de reprodução (uma cópia sem perdas ocuparia o mesmo que o original)."""
    from .transcode import BROWSER_PIX_FMTS, BROWSER_STREAM_CODECS

    if not _ffmpeg():
        return False
    try:
        codec, pix = probe_codec(src)
    except (OSError, subprocess.SubprocessError):
        return False
    return codec in BROWSER_STREAM_CODECS and (not pix or pix in BROWSER_PIX_FMTS)


def remux_for_analysis(src: Path, stem: Path) -> tuple[Path, dict[str, Any]]:
    """Cópia de análise sem perdas (-c copy para MP4, reproduzível no browser) de um vídeo cujo contentor o browser
    não abre. Verificada: os mesmos pacotes de vídeo, ou (H.264/HEVC) as mesmas fatias codificadas e parâmetros."""
    if not _ffmpeg():
        raise ValueError("ffmpeg indisponível: o original fica como cópia analisada")
    codec, _ = probe_codec(src)
    original_hash, original_packets = stream_hash(src)
    out, part = stem.with_name(stem.name + ".mp4"), stem.with_name(stem.name + ".part.mp4")
    sec_format._check_space(stem.parent, src.stat().st_size + sec_format.SPACE_MARGIN)
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-fflags", "+genpts", "-i", str(src), "-map",
                        "0:v:0", "-c", "copy", "-movflags", "+faststart", str(part)], check=True,
                       capture_output=True, timeout=7200)
        copy_hash, copy_packets = stream_hash(part)
        info: dict[str, Any] = {"method": REMUX_METHOD, "container": "mp4 (-c copy, sem recodificação)",
                                "codec": codec, "packets": copy_packets, "original_streamhash": original_hash,
                                "streamhash": copy_hash}
        if copy_hash == original_hash and copy_packets == original_packets:
            info["verified_by"] = "pacotes de vídeo iguais (streamhash)"
        elif codec in ("h264", "hevc"):  # Annex B → avcC/hvcC: os pacotes mudam de forma, as fatias não
            original_fp, copy_fp = nal_fingerprint(src, codec), nal_fingerprint(part, codec)
            if not sec_format.same_video(original_fp, copy_fp):
                raise ValueError("a cópia por remux não tem as mesmas fatias de vídeo que o original")
            info.update(copy_fp, verified_by="fatias codificadas (VCL) e parâmetros iguais")
        else:
            digest = decoded_hash(src)
            if decoded_hash(part) != digest:
                raise ValueError("os frames descodificados da cópia diferem dos do original")
            info.update(decoded_sha256=digest, verified_by="frames descodificados iguais")
    except subprocess.CalledProcessError as exc:
        part.unlink(missing_ok=True)
        raise ValueError(f"remux falhou: {(exc.stderr or b'').decode('utf-8', 'replace')[-300:]}") from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(out)
    info["analysis_sha256"] = file_sha256(out)
    return out, info


def retime_sec_copy(video: Video, stem: Path, fps: float, fps_source: str) -> tuple[Path, dict[str, Any]]:
    """Refaz a cópia de um .sec com outro fps a partir da própria cópia (não precisa do original): a sequência
    H.264 sai da cópia e volta a entrar num MP4 com o novo ritmo, sem recodificação; as fatias têm de ser as mesmas."""
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
    fp = sec_format.NalFingerprint()
    try:
        while chunk := reader.stdout.read(CHUNK):  # type: ignore[union-attr]
            fp.update(chunk)
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
    expected = x if x.get("vcl_sha256") else nal_fingerprint(src)  # extrações antigas: a cópia atual
    if not sec_format.same_video(fp.result(), expected):
        part.unlink(missing_ok=True)
        raise ValueError("as fatias de vídeo da nova cópia não coincidem com as da cópia atual")
    part.replace(out)
    return out, {**x, "fps": fps, "fps_source": fps_source, "fps_assumed": False,
                 "container": "mp4 (-c copy, sem recodificação)", "analysis_sha256": file_sha256(out)}


def verify_copy(video: Video) -> tuple[bool, str]:
    """A cópia de análise contém exatamente o vídeo do original? (antes de o apagar)

    Devolve (ok, motivo da falha | descrição da verificação)."""
    x = video.extraction or {}
    copy = Path(video.analysis_path or "")
    if not video.analysis_path or not copy.exists():
        return False, "sem cópia de análise"
    if video.analysis_sha256 and file_sha256(copy) != video.analysis_sha256:
        return False, "a cópia de análise não corresponde ao SHA-256 registado"
    try:
        if x.get("method") == REMUX_METHOD:
            if x.get("vcl_sha256"):
                ok = sec_format.same_video(nal_fingerprint(copy, x.get("codec", "h264")), x)
                return (True, x["vcl_sha256"]) if ok else (False, "as fatias de vídeo da cópia diferem do original")
            if x.get("decoded_sha256"):
                ok = decoded_hash(copy) == x["decoded_sha256"]
                return (True, x["decoded_sha256"]) if ok else (False, "os frames da cópia diferem dos do original")
            digest, packets = stream_hash(copy)
            if digest != x.get("original_streamhash") or packets != x.get("packets"):
                return False, "os pacotes de vídeo da cópia diferem dos do original"
            return True, digest
        if x.get("method") == sec_format.METHOD:
            # extrações antigas não guardaram a impressão: lê-se do próprio .sec (o original ainda está aqui)
            expected = x if x.get("vcl_sha256") else sec_format.payload_fingerprint(video.stored_path)
            if not sec_format.same_video(nal_fingerprint(copy), expected):
                return False, "as fatias de vídeo da cópia diferem das do original"
            return True, expected["vcl_sha256"]
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
        video.extraction = {**(video.extraction or {}), "original_kept_reason": reason}
        s.add(video)
        s.commit()
        log_event(s, "video.original_kept", case_id=video.case_id, actor=actor,
                  details={"video_id": video.id, "filename": video.filename, "reason": reason})
        return False
    size = original.stat().st_size
    original.unlink()
    video.original_removed_at, video.analysis_streamhash = utcnow(), detail
    video.extraction = {k: v for k, v in (video.extraction or {}).items() if k != "original_kept_reason"}
    s.add(video)
    s.commit()
    log_event(s, "video.original_removed", case_id=video.case_id, actor=actor,
              details={"video_id": video.id, "filename": video.filename, "original_sha256": video.sha256,
                       "original_size_bytes": size, "analysis_sha256": video.analysis_sha256,
                       "video_fingerprint": detail,
                       "verified_by": (video.extraction or {}).get("verified_by", "fatias codificadas (VCL)"),
                       "method": (video.extraction or {}).get("method"),
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


def pending_removal(s: Session) -> list[int]:
    """Vídeos convertidos que ainda têm o original no servidor (ex.: de versões anteriores, ou em que a verificação
    antiga falhava): candidatos a remover no arranque."""
    from sqlmodel import select

    q = select(Video.id).where(Video.original_removed_at.is_(None), Video.analysis_path.is_not(None),
                               Video.extraction_status == "ready")
    return list(s.exec(q))


def remove_pending() -> int:
    """No arranque (thread): verifica as cópias existentes e apaga os originais que passam a verificação."""
    from ..db import get_engine

    if not enabled():
        return 0
    with Session(get_engine()) as s:
        ids = pending_removal(s)
    removed = 0
    for vid in ids:
        try:
            with Session(get_engine()) as s:
                video = s.get(Video, vid)
                if video is not None and Path(video.stored_path).exists() and remove_original(s, video):
                    removed += 1
        except Exception:  # noqa: BLE001 - um vídeo com problemas não impede os outros
            log.exception("Remoção do original do vídeo %s falhou", vid)
    if removed:
        log.info("Originais convertidos removidos no arranque: %d", removed)
    return removed


def start_pending_removal() -> None:
    import threading

    threading.Thread(target=remove_pending, name="faceai-originals", daemon=True).start()
