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
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAGIC = b"\x00\xe0"  # bytes 0-1; byte 2 = 02 e byte 3 = canal da câmara (03 em 12000300.sec, 04 em …0400.sec)
HEADER = 48
START_SCAN = 64  # a carga pode trazer alguns bytes antes do 1.º start code H.264
MAX_SIDE = 8192
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
    channel: int = 0  # byte 3


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
        head = fh.read(HEADER + START_SCAN)
    return _parse_header(head, 0) is not None and _start_code_at(head[HEADER:]) is not None


def _parse_header(head: bytes, offset: int) -> Block | None:
    if len(head) < HEADER or head[:2] != MAGIC:
        return None
    length = int.from_bytes(head[21:24], "big")
    width, height = int.from_bytes(head[24:28], "big"), int.from_bytes(head[28:32], "big")
    if length <= HEADER or length > MAX_BLOCK or not (16 <= width <= MAX_SIDE and 16 <= height <= MAX_SIDE):
        return None
    return Block(offset=offset, length=length, frame_type=head[4], width=width, height=height,
                 stamp=head[12:20], channel=head[3])


def _start_code_at(data: bytes) -> int | None:
    """Posição do 1.º start code H.264 (00 00 01, ou 00 00 00 01) nos primeiros START_SCAN bytes da carga."""
    i = data[:START_SCAN + 3].find(b"\x00\x00\x01")
    if i < 0:
        return None
    return i - 1 if i > 0 and data[i - 1] == 0 else i


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
            start = _start_code_at(payload)
            if start is None:
                stats["non_h264_blocks"] += 1  # ex.: áudio ou dados do fabricante
            else:
                if start:
                    stats["payload_prefix_bytes"] = stats.get("payload_prefix_bytes", 0) + start
                yield block, payload[start:]
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
            head = fh.read(HEADER + START_SCAN)
            if _parse_header(head, pos + i) and _start_code_at(head[HEADER:]) is not None:
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
    channels: set[int] = set()
    for block, payload in iter_blocks(path, stats):
        channels.add(block.channel)
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
            first.append({"offset": block.offset, "length": block.length, "channel": block.channel,
                          "type": FRAME_TYPES.get(block.frame_type, str(block.frame_type)),
                          "width": block.width, "height": block.height, "header_hex": head.hex(" ")})
    return {"format": "sec", "size_bytes": Path(path).stat().st_size, **stats,
            "channels": sorted(channels),
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


class NalFingerprint:
    """Impressão de uma sequência Annex B (H.264/HEVC) independente do contentor.

    SHA-256 das NAL de imagem (VCL), pela ordem, e conjunto dos parâmetros (SPS/PPS/VPS). Um remux -c copy (ex.:
    para MP4 e de volta a Annex B) pode mudar os start codes (3/4 bytes), os zeros de preenchimento, os AUD/SEI e a
    repetição dos parâmetros, mas não as fatias codificadas: com as mesmas fatias e os mesmos parâmetros, os frames
    descodificados são os mesmos."""

    def __init__(self, codec: str = "h264") -> None:
        self.hevc = codec in ("hevc", "h265")
        self._vcl = hashlib.sha256()
        self.vcl_nals = 0
        self.params: set[str] = set()
        self._buf = b""

    def update(self, data: bytes) -> None:
        buf = self._buf + data
        starts, i = [], buf.find(b"\x00\x00\x01")
        while i >= 0:
            starts.append(i)
            i = buf.find(b"\x00\x00\x01", i + 3)
        if len(starts) < 2:
            self._buf = buf
            return
        for a, b in zip(starts, starts[1:], strict=False):
            self._nal(buf[a + 3:b])
        self._buf = buf[starts[-1]:]

    def _nal(self, nal: bytes) -> None:
        nal = nal.rstrip(b"\x00")  # zeros entre NAL (e o 1.º zero dos start codes de 4 bytes)
        if not nal:
            return
        kind = (nal[0] >> 1) & 0x3F if self.hevc else nal[0] & 0x1F
        vcl = kind <= 31 if self.hevc else 1 <= kind <= 5
        params = kind in (32, 33, 34) if self.hevc else kind in (7, 8)
        if vcl:
            self._vcl.update(len(nal).to_bytes(4, "big") + nal)
            self.vcl_nals += 1
        elif params:
            self.params.add(hashlib.sha256(nal).hexdigest())

    def result(self) -> dict[str, Any]:
        if self._buf.startswith(b"\x00\x00\x01") or self._buf.startswith(b"\x00\x00\x00\x01"):
            self._nal(self._buf[self._buf.find(b"\x00\x00\x01") + 3:])
        self._buf = b""
        return {"vcl_sha256": self._vcl.hexdigest(), "vcl_nals": self.vcl_nals, "param_sets": sorted(self.params)}


def same_video(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a.get("vcl_sha256") == b.get("vcl_sha256") and a.get("vcl_nals") == b.get("vcl_nals")
            and sorted(a.get("param_sets") or []) == sorted(b.get("param_sets") or []))


def payload_fingerprint(src: str | Path) -> dict[str, Any]:
    """Impressão do vídeo de um .sec, lida diretamente dos blocos do original (stream principal)."""
    src = Path(src)
    summary = inspect(src, sample=0)
    if not summary["streams"]:
        raise ValueError("ficheiro .sec sem blocos de vídeo H.264 reconhecíveis")
    fp = NalFingerprint()
    for _, payload in _main_payloads(src, summary["streams"][0]):
        fp.update(payload)
    return fp.result()


class NoSpace(OSError):
    """Disco sem espaço para a cópia de trabalho (mensagem clara para o analista)."""


SPACE_MARGIN = 200 * 1024 * 1024


def _free_space(folder: Path) -> int:
    return shutil.disk_usage(folder).free


def _check_space(folder: Path, needed: int) -> None:
    free = _free_space(folder)
    if free < needed:
        raise NoSpace(f"espaço insuficiente no disco para extrair o vídeo: são precisos {needed / 1e9:.2f} GB, "
                      f"livres {free / 1e9:.2f} GB")


def _main_payloads(src: Path, main: dict[str, Any], progress=None):
    """Cargas H.264 do stream principal, a partir do 1.º frame-chave: (bloco, carga)."""
    started = False
    size = src.stat().st_size or 1
    for block, payload in iter_blocks(src):
        if progress:
            progress(block.offset / size)
        if (block.width, block.height) != (main["width"], main["height"]):
            continue
        if not started and block.frame_type != 1 and not has_sps(payload):
            continue  # sem SPS/PPS antes do 1.º frame-chave, os frames P não são descodificáveis
        started = True
        yield block, payload


def extract(src: str | Path, dest_stem: Path, *, default_fps: float = 25.0, fps: float | None = None,
            fps_source: str = "", progress=None) -> tuple[Path, dict[str, Any]]:
    """Extrai o stream principal (a resolução com mais dados) para uma cópia de trabalho.

    Uma só escrita: com ffmpeg, as cargas H.264 seguem diretamente para o ffmpeg (stdin) e só o MP4 é gravado
    (-c copy, sem recodificação); sem ffmpeg, grava-se a sequência .h264. O SHA-256 H.264 é calculado pelo caminho.

    fps: força o fps da cópia (correção, ex.: o DVR grava a 12 fps e o SPS não o indica); fps_source diz de onde
    vem. A sequência H.264 é a mesma: só muda o ritmo com que o MP4 a apresenta. progress(fração 0–1).

    Devolve (ficheiro de análise, relatório). Erro (ValueError) se o ficheiro não tiver vídeo H.264 reconhecível;
    NoSpace se o disco não tiver espaço para a cópia.
    """
    src = Path(src)
    summary = inspect(src, sample=4)
    if not summary["streams"]:
        head = first_bytes(src)
        raise ValueError(
            "ficheiro .sec sem blocos de vídeo H.264 reconhecíveis (blocos sem H.264: "
            f"{summary['non_h264_blocks']}, ressincronizações: {summary['resyncs']}, "
            f"bytes ignorados: {summary['skipped_bytes']}; primeiros bytes: {head})")
    main = summary["streams"][0]
    _check_space(dest_stem.parent, int(main["h264_bytes"] * 1.05) + SPACE_MARGIN)
    first_key = next((p for _, p in _main_payloads(src, main)), None)
    if first_key is None:
        raise ValueError("ficheiro .sec sem nenhum frame-chave H.264")
    assumed = False
    if fps:
        source = fps_source or "indicado"
    elif sps_fps := _first_key_fps(first_key, dest_stem):
        fps, source = sps_fps, "SPS (VUI)"
    else:
        fps, source, assumed = default_fps, f"valor de recurso ({default_fps:g})", True
    h, fp = hashlib.sha256(), NalFingerprint()
    counts = {"frames": 0, "keyframes": 0, "bytes": 0}

    def feed(out) -> None:
        for block, payload in _main_payloads(src, main, progress):
            out.write(payload)
            h.update(payload)
            fp.update(payload)
            counts["frames"] += 1
            counts["keyframes"] += block.frame_type == 1
            counts["bytes"] += len(payload)

    info: dict[str, Any] = {"method": METHOD, "fps": fps, "fps_source": source, "fps_assumed": assumed}
    analysis = None
    if shutil.which("ffmpeg") is not None:
        mp4 = dest_stem.with_name(dest_stem.name + ".mp4")
        part = dest_stem.with_name(dest_stem.name + ".part.mp4")
        cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-fflags", "+genpts", "-r", f"{fps:g}",
               "-f", "h264", "-i", "pipe:0", "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart", str(part)]
        with tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
            try:
                try:
                    feed(proc.stdin)
                    proc.stdin.close()
                except BrokenPipeError:  # o ffmpeg terminou antes do fim: trata-se como falha do MP4
                    pass
                code = proc.wait(timeout=3600)
            except BaseException:
                proc.kill()
                proc.wait()
                part.unlink(missing_ok=True)
                raise
            if code == 0:
                part.replace(mp4)
                analysis, info["container"] = mp4, "mp4 (-c copy, sem recodificação)"
            else:
                part.unlink(missing_ok=True)
                err.seek(0)
                info["remux_error"] = err.read().decode("utf-8", "replace")[-500:]
                h, fp, counts = hashlib.sha256(), NalFingerprint(), {"frames": 0, "keyframes": 0, "bytes": 0}
    if analysis is None:  # sem ffmpeg (ou o MP4 falhou): fica a sequência H.264 crua, que o OpenCV também lê
        raw = dest_stem.with_name(dest_stem.name + ".h264")
        with raw.open("wb") as out:
            feed(out)
        analysis, info["container"] = raw, "h264"
    info.update({
        "frames": counts["frames"], "keyframes": counts["keyframes"],
        "frames_before_first_keyframe": main["frames"] - counts["frames"],
        "width": main["width"], "height": main["height"],
        "h264_sha256": h.hexdigest(), "h264_bytes": counts["bytes"], **fp.result(),
        "resyncs": summary["resyncs"], "skipped_bytes": summary["skipped_bytes"],
        "non_h264_blocks": summary["non_h264_blocks"],
        "streams": summary["streams"], "first_blocks": summary["first_blocks"],
    })
    info["analysis_sha256"] = _sha256(analysis)
    return analysis, info


def _first_key_fps(payload: bytes, dest_stem: Path) -> float | None:
    """fps do SPS do 1.º frame-chave (só essa carga vai para um ficheiro temporário)."""
    tmp = dest_stem.with_name(dest_stem.name + ".sps.h264")
    try:
        tmp.write_bytes(payload)
        return _sps_fps(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def first_bytes(path: str | Path, n: int = 64) -> str:
    with open(path, "rb") as fh:
        return fh.read(n).hex(" ")


def header_summary(path: str | Path, limit: int = 64) -> dict[str, Any] | None:
    """Validação rápida (na importação): primeiros blocos válidos, sem ler o ficheiro todo."""
    blocks = []
    for block, _ in iter_blocks(path):
        blocks.append(block)
        if len(blocks) >= limit:
            break
    if not blocks:
        return None
    sizes: dict[tuple[int, int], int] = {}
    for b in blocks:
        sizes[(b.width, b.height)] = sizes.get((b.width, b.height), 0) + b.length
    width, height = max(sizes, key=sizes.get)
    return {"width": width, "height": height, "blocks": len(blocks)}
