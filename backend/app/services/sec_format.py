"""Ficheiros .sec de DVR (exportação com leitor .exe do fabricante): extração do vídeo H.264 contido.

Formato (inferido de exportações reais; não há documentação pública do fabricante):

    Cada frame é um bloco = cabeçalho de 48 bytes + carga H.264 Annex B (começa por 00 00 00 01).
      0-3   magic 00 e0 02 03
      4     tipo do frame: 01 = chave (com SPS/PPS), 02 = P
      5-20  desconhecido (6-11 constantes; 12-19 variam de frame para frame, provavelmente a hora)
      21-23 comprimento do bloco (cabeçalho incluído), big-endian 24 bits → o bloco seguinte começa em +comprimento
      24-27 largura (big-endian)    28-31 altura (big-endian)
      32-47 16 bytes por frame (talvez um hash)

O original .sec nunca é alterado (é a evidência, com o seu SHA-256). A análise usa uma cópia de trabalho com as
cargas H.264 dos blocos de vídeo, pela mesma ordem, sem qualquer recodificação; com ffmpeg, essa sequência é
apenas colocada num contentor MP4 (-c copy) para o OpenCV saber o n.º de frames e poder posicionar-se. O
relatório de extração (blocos, frames-chave, bytes ignorados, ressincronizações, SHA-256 da sequência H.264 e
do ficheiro de análise) fica no vídeo e na auditoria, e permite repetir e confirmar a extração.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAGIC = b"\x00\xe0\x02\x03"
HEADER = 48
START_CODES = (b"\x00\x00\x00\x01", b"\x00\x00\x01")
MAX_BLOCK = 16 * 1024 * 1024  # um frame maior do que isto é um comprimento corrompido
CHUNK = 8 * 1024 * 1024
FRAME_TYPES = {1: "chave", 2: "P"}
METHOD = "sec-demux-1"  # versão do método de extração (fica no relatório; muda se as regras mudarem)


@dataclass
class Block:
    offset: int
    length: int
    frame_type: int
    width: int
    height: int
    stamp: bytes  # bytes 12-19 (desconhecidos; provavelmente a hora)


@dataclass
class _Stream:
    width: int
    height: int
    frames: int = 0
    keyframes: int = 0
    bytes: int = 0
    first_offset: int = 0
    types: dict[int, int] = field(default_factory=dict)


def is_sec(path: str | Path) -> bool:
    with open(path, "rb") as fh:
        head = fh.read(HEADER)
    return len(head) == HEADER and head[:4] == MAGIC


def _parse_header(head: bytes, offset: int) -> Block | None:
    if len(head) < HEADER or head[:4] != MAGIC:
        return None
    length = int.from_bytes(head[21:24], "big")
    if length <= HEADER or length > MAX_BLOCK:
        return None
    return Block(offset=offset, length=length, frame_type=head[4],
                 width=int.from_bytes(head[24:28], "big"), height=int.from_bytes(head[28:32], "big"),
                 stamp=head[12:20])


def iter_blocks(path: str | Path, stats: dict[str, Any] | None = None):
    """Percorre os blocos (Block, carga). Um cabeçalho inválido ou uma carga sem start code H.264 faz procurar o
    próximo magic (ressincronização); os bytes saltados são contados em stats."""
    stats = stats if stats is not None else {}
    stats.setdefault("resyncs", 0)
    stats.setdefault("skipped_bytes", 0)
    stats.setdefault("non_h264_blocks", 0)
    size = Path(path).stat().st_size
    with open(path, "rb") as fh:
        pos = 0
        while pos + HEADER <= size:
            fh.seek(pos)
            head = fh.read(HEADER)
            block = _parse_header(head, pos)
            payload = fh.read(block.length - HEADER) if block and pos + block.length <= size else b""
            if block is None or not payload:
                nxt = _find_magic(fh, pos + 1, size)
                stats["resyncs"] += 1
                stats["skipped_bytes"] += (nxt if nxt is not None else size) - pos
                if nxt is None:
                    return
                pos = nxt
                continue
            if not payload.startswith(START_CODES):
                stats["non_h264_blocks"] += 1  # ex.: áudio ou dados do fabricante
            else:
                yield block, payload
            pos += block.length
        if pos < size:
            stats["skipped_bytes"] += size - pos  # restos no fim (cabeçalho truncado)


def _find_magic(fh, start: int, size: int) -> int | None:
    pos = start
    while pos < size:
        fh.seek(pos)
        buf = fh.read(CHUNK + len(MAGIC) - 1)
        if not buf:
            return None
        i = buf.find(MAGIC)
        while i >= 0:
            fh.seek(pos + i)
            if _parse_header(fh.read(HEADER), pos + i):
                return pos + i
            i = buf.find(MAGIC, i + 1)
        pos += CHUNK
    return None


def has_sps(payload: bytes) -> bool:
    """A carga traz um SPS (NAL tipo 7) nos primeiros bytes: é um ponto de entrada descodificável."""
    head = payload[:512]
    i = head.find(b"\x00\x00\x01")
    while 0 <= i < len(head) - 3:
        if head[i + 3] & 0x1F == 7:
            return True
        i = head.find(b"\x00\x00\x01", i + 3)
    return False


def inspect(path: str | Path, sample: int = 8) -> dict[str, Any]:
    """Resumo do ficheiro sem extrair nada: streams (por resolução), tipos de frame e os primeiros cabeçalhos."""
    stats: dict[str, Any] = {}
    streams: dict[tuple[int, int], _Stream] = {}
    first: list[dict[str, Any]] = []
    for block, payload in iter_blocks(path, stats):
        st = streams.setdefault((block.width, block.height),
                                _Stream(block.width, block.height, first_offset=block.offset))
        st.frames += 1
        st.keyframes += block.frame_type == 1
        st.bytes += len(payload)
        st.types[block.frame_type] = st.types.get(block.frame_type, 0) + 1
        if len(first) < sample:
            with open(path, "rb") as fh:
                fh.seek(block.offset)
                head = fh.read(HEADER)
            first.append({"offset": block.offset, "length": block.length,
                          "type": FRAME_TYPES.get(block.frame_type, str(block.frame_type)),
                          "width": block.width, "height": block.height, "header_hex": head.hex(" ")})
    return {"format": "sec", "size_bytes": Path(path).stat().st_size, **stats,
            "streams": [_stream_dict(s) for s in sorted(streams.values(), key=lambda s: -s.bytes)],
            "first_blocks": first}


def _stream_dict(s: _Stream) -> dict[str, Any]:
    return {"width": s.width, "height": s.height, "frames": s.frames, "keyframes": s.keyframes,
            "h264_bytes": s.bytes, "first_offset": s.first_offset,
            "frame_types": {FRAME_TYPES.get(k, str(k)): v for k, v in sorted(s.types.items())}}


def _sps_fps(path: Path) -> float | None:
    """fps declarado no SPS (VUI: time_scale / (2 × num_units_in_tick)), lido pelo ffmpeg (trace_headers).

    O ffprobe não serve: numa sequência H.264 crua devolve 25 por omissão ou o dobro do valor."""
    if shutil.which("ffmpeg") is None:
        return None
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-f", "h264", "-i", str(path), "-c", "copy",
           "-bsf:v", "trace_headers", "-frames:v", "1", "-f", "null", "-"]
    try:
        err = subprocess.run(cmd, capture_output=True, timeout=60).stderr.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - sem informação de fps: usa-se o valor de recurso
        return None
    tick = re.search(r"num_units_in_tick\s+[01]+ = (\d+)", err)
    scale = re.search(r"time_scale\s+[01]+ = (\d+)", err)
    if not tick or not scale or int(tick.group(1)) == 0:
        return None
    fps = int(scale.group(1)) / (2 * int(tick.group(1)))
    return round(fps, 3) if 1 <= fps <= 120 else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def extract(src: str | Path, dest_stem: Path, *, default_fps: float = 25.0) -> tuple[Path, dict[str, Any]]:
    """Extrai o stream principal (a resolução com mais dados) para uma cópia de trabalho.

    Devolve (ficheiro de análise, relatório). Erro (ValueError) se o ficheiro não tiver vídeo H.264 reconhecível.
    """
    src = Path(src)
    summary = inspect(src, sample=4)
    if not summary["streams"]:
        raise ValueError("ficheiro .sec sem blocos de vídeo H.264 reconhecíveis")
    main = summary["streams"][0]
    raw = dest_stem.with_name(dest_stem.name + ".h264")
    h = hashlib.sha256()
    frames = keyframes = 0
    started = False
    with raw.open("wb") as out:
        for block, payload in iter_blocks(src):
            if (block.width, block.height) != (main["width"], main["height"]):
                continue
            if not started and block.frame_type != 1 and not has_sps(payload):
                continue  # sem SPS/PPS antes do 1.º frame-chave, os frames P não são descodificáveis
            started = True
            out.write(payload)
            h.update(payload)
            frames += 1
            keyframes += block.frame_type == 1
    if frames == 0:
        raw.unlink(missing_ok=True)
        raise ValueError("ficheiro .sec sem nenhum frame-chave H.264")
    fps = _sps_fps(raw)
    info: dict[str, Any] = {
        "method": METHOD, "container": "h264", "frames": frames, "keyframes": keyframes,
        "frames_before_first_keyframe": main["frames"] - frames,
        "width": main["width"], "height": main["height"],
        "fps": fps or default_fps, "fps_source": "SPS (VUI)" if fps else f"valor de recurso ({default_fps:g})",
        "h264_sha256": h.hexdigest(), "h264_bytes": raw.stat().st_size,
        "resyncs": summary["resyncs"], "skipped_bytes": summary["skipped_bytes"],
        "non_h264_blocks": summary["non_h264_blocks"],
        "streams": summary["streams"], "first_blocks": summary["first_blocks"],
    }
    analysis = raw
    if shutil.which("ffmpeg") is not None:
        mp4 = dest_stem.with_name(dest_stem.name + ".mp4")
        part = dest_stem.with_name(dest_stem.name + ".part.mp4")
        cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-fflags", "+genpts", "-r", f"{info['fps']:g}",
               "-f", "h264", "-i", str(raw), "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart", str(part)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=3600)
            part.replace(mp4)
            raw.unlink()
            analysis, info["container"] = mp4, "mp4 (-c copy, sem recodificação)"
        except Exception as exc:  # noqa: BLE001 - fica a sequência H.264 crua, que o OpenCV também lê
            part.unlink(missing_ok=True)
            info["remux_error"] = str(getattr(exc, "stderr", b"") or exc)[:500]
    info["analysis_sha256"] = _sha256(analysis)
    return analysis, info
