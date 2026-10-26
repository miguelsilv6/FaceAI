"""Frames originais (sem anotações) das deteções e ZIP com todos os frames validados pelo utilizador.

Validado = deteção com revisão "confirmada" (a revisão de um track aplica-se a todas as suas deteções). O frame é
extraído do vídeo original e verificado contra o recorte guardado na análise (ver services/frames.py); fica em
cache em PNG na pasta da análise.

O ZIP é gerado em streaming: com muitos frames a extração pode demorar minutos e o cliente (ou o Cloudflare, que
corta pedidos sem resposta ao fim de 100 s) começa logo a receber dados. Inclui um manifesto CSV com o SHA-256 de
cada PNG e a identificação de cada deteção.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import cv2
from sqlmodel import Session, select

from ..models import Detection, Job, ReviewStatus, SavedFrame, Video
from .aspect import job_display
from .clock import format_real, real_time
from .frames import extract_frame
from .jobs import job_dir
from .locations import locations_by_id, video_place
from .video_processor import format_timestamp


def original_frame_png(job: Job, video: Video, det: Detection) -> tuple[bytes, str, float | None]:
    """(PNG, método de extração, erro do recorte). Levanta ValueError se o frame não puder ser lido."""
    cache = job_dir(job) / "originals" / f"v{video.id:04d}_f{det.frame_index:07d}.png"
    meta_path = cache.with_suffix(".json")
    if cache.exists():
        body = cache.read_bytes()
        try:  # método da extração original (o PNG em cache é o mesmo ficheiro)
            meta = json.loads(meta_path.read_text())
            return body, f"{meta['method']} (cache)", meta.get("crop_error")
        except (OSError, ValueError, KeyError):
            return body, "cache", None
    extracted = extract_frame(video.source_path, det.frame_index, det.crop_path, (det.x, det.y, det.w, det.h),
                              margin=0.05 if det.kind == "body" else 0.25, display=job_display(job, video.id))
    ok, buf = cv2.imencode(".png", extracted.image)
    if not ok:
        raise ValueError("Falha ao codificar o frame.")
    body = buf.tobytes()
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(body)
    tmp.replace(cache)
    meta_path.write_text(json.dumps({"method": extracted.method, "crop_error": extracted.crop_error}))
    return body, extracted.method, extracted.crop_error


@dataclass
class ValidatedSet:
    """Deteções confirmadas, já carregadas da base de dados (o ZIP é gerado depois de o pedido a libertar)."""

    items: list[tuple[Detection, Job, Video]] = field(default_factory=list)
    frames: list[tuple[SavedFrame, Job, Video]] = field(default_factory=list)  # guardados no leitor
    places: dict[int, dict] = field(default_factory=dict)  # vídeo → local e câmara

    @property
    def detection_ids(self) -> list[int]:
        return [d.id for d, _, _ in self.items]


def collect(s: Session, jobs: list[Job]) -> ValidatedSet:
    out = ValidatedSet()
    by_id = {j.id: j for j in jobs}
    if not by_id:
        return out
    q = (select(Detection).where(Detection.job_id.in_(list(by_id)), Detection.review_status == ReviewStatus.confirmed)
         .order_by(Detection.job_id, Detection.video_id, Detection.frame_index, Detection.id))
    videos: dict[int, Video | None] = {}
    for det in s.exec(q):
        if det.video_id not in videos:
            videos[det.video_id] = s.get(Video, det.video_id)
        video = videos[det.video_id]
        if video is not None:
            s.expunge(det)
            out.items.append((det, by_id[det.job_id], video))
    q = select(SavedFrame).where(SavedFrame.job_id.in_(list(by_id))).order_by(SavedFrame.job_id, SavedFrame.id)
    for sf in s.exec(q):
        if sf.video_id not in videos:
            videos[sf.video_id] = s.get(Video, sf.video_id)
        video = videos[sf.video_id]
        if video is not None:
            s.expunge(sf)
            out.frames.append((sf, by_id[sf.job_id], video))
    locs: dict[int, dict] = {}
    for v in videos.values():
        if v is not None:
            if v.case_id not in locs:
                locs[v.case_id] = locations_by_id(s, v.case_id)
            out.places[v.id] = video_place(v, locs[v.case_id])
            s.expunge(v)
    for j in jobs:
        s.expunge(j)
    return out


class _Sink(io.RawIOBase):
    """Destino sem seek para o zipfile: acumula os bytes escritos até serem enviados."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:  # type: ignore[override]
        self.chunks.append(bytes(b))
        return len(b)

    def take(self) -> bytes:
        data = b"".join(self.chunks)
        self.chunks.clear()
        return data


MANIFEST_FIELDS = [
    "ficheiro", "sha256", "analise", "deteccao", "tipo", "track", "video_id", "video", "local", "camara", "frame",
    "tempo_video", "hora_real", "semelhanca", "distancia", "revisto_por", "revisto_em", "nota", "extracao",
    "erro_recorte", "proporcao",
]


def _resized(job: Job, video: Video, saved: SavedFrame | None = None) -> str:
    """Coluna "proporcao": vazio, ou o redimensionamento aplicado (píxeis não quadrados, services/aspect.py).

    Um frame guardado no leitor tem o tamanho com que foi gravado (pode ser anterior à análise atual)."""
    size = (saved.width, saved.height) if saved is not None else job_display(job, video.id)
    if not size or not size[0] or tuple(size) == (video.width, video.height):
        return ""
    return f"{video.width}x{video.height} -> {size[0]}x{size[1]} (bicubica)"


def stream_zip(validated: ValidatedSet, *, scope: str, actor: str) -> Iterator[bytes]:
    sink = _Sink()
    manifest = io.StringIO()
    writer = csv.DictWriter(manifest, fieldnames=MANIFEST_FIELDS)
    writer.writeheader()
    done: dict[tuple[int, int, int], tuple[str, str, str, float | None]] = {}  # (job, vídeo, frame) → ficheiro
    failures: list[str] = []
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED) as zf:
        for det, job, video in validated.items:
            key = (job.id, video.id, det.frame_index)
            if key not in done:
                name = (f"analise{job.id:05d}/video{video.id:04d}_frame{det.frame_index:07d}_"
                        f"{format_timestamp(det.timestamp_s).replace(':', '-')}.png")
                try:
                    if not Path(video.source_path).exists():
                        raise ValueError("o vídeo original já não está disponível")
                    body, method, err = original_frame_png(job, video, det)
                except ValueError as exc:
                    failures.append(f"Deteção {det.id} (análise {job.id}, vídeo {video.filename}, "
                                    f"frame {det.frame_index}): {exc}")
                    done[key] = ("", "", f"falhou: {exc}", None)
                else:
                    zf.writestr(name, body)
                    done[key] = (name, hashlib.sha256(body).hexdigest(), method, err)
                    yield sink.take()
            name, sha, method, err = done[key]
            rt = real_time(video.dvr_start, video.dvr_rate, video.clock_offset_s, det.timestamp_s)
            writer.writerow({
                "ficheiro": name, "sha256": sha, "analise": job.id, "deteccao": det.id, "tipo": det.kind,
                "track": det.track_id or "", "video_id": video.id, "video": video.filename,
                "local": validated.places.get(video.id, {}).get("location_name", ""),
                "camara": validated.places.get(video.id, {}).get("camera_name", video.filename),
                "frame": det.frame_index, "tempo_video": format_timestamp(det.timestamp_s),
                "hora_real": format_real(rt), "semelhanca": round(det.similarity, 4),
                "distancia": round(det.distance, 4), "revisto_por": det.reviewed_by,
                "revisto_em": det.reviewed_at.isoformat() if det.reviewed_at else "", "nota": det.review_note,
                "extracao": method, "erro_recorte": "" if err is None else err, "proporcao": _resized(job, video),
            })
        for sf, job, video in validated.frames:
            name = (f"analise{job.id:05d}/video{video.id:04d}_frame{sf.frame_index:07d}_"
                    f"{format_timestamp(sf.timestamp_s).replace(':', '-')}_guardado{sf.id:05d}.png")
            try:
                body = Path(sf.stored_path).read_bytes()
            except OSError as exc:
                failures.append(f"Frame guardado {sf.id} (análise {job.id}, vídeo {video.filename}): {exc}")
                name, sha = "", ""
            else:
                zf.writestr(name, body)
                sha = hashlib.sha256(body).hexdigest()
                if sha != sf.sha256:
                    failures.append(f"Frame guardado {sf.id}: SHA-256 diferente do registado ({sf.sha256}).")
                yield sink.take()
            rt = real_time(video.dvr_start, video.dvr_rate, video.clock_offset_s, sf.timestamp_s)
            writer.writerow({
                "ficheiro": name, "sha256": sha, "analise": job.id, "deteccao": "", "tipo": "frame guardado",
                "track": "", "video_id": video.id, "video": video.filename,
                "local": validated.places.get(video.id, {}).get("location_name", ""),
                "camara": validated.places.get(video.id, {}).get("camera_name", video.filename),
                "frame": sf.frame_index, "tempo_video": format_timestamp(sf.timestamp_s),
                "hora_real": format_real(rt), "semelhanca": "", "distancia": "", "revisto_por": sf.created_by,
                "revisto_em": sf.created_at.isoformat() if sf.created_at else "", "nota": sf.note,
                "extracao": "guardado no leitor", "erro_recorte": "", "proporcao": _resized(job, video, sf),
            })
        zf.writestr("manifesto.csv", "﻿" + manifest.getvalue())
        frames = sum(1 for v in done.values() if v[0])
        readme = [
            "FaceAI — frames originais validados",
            "",
            f"Âmbito: {scope}",
            f"Gerado por: {actor}",
            f"Gerado em (UTC): {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "Critério: deteções com revisão humana \"confirmada\" (a revisão de um track aplica-se às suas deteções).",
            "Cada PNG é o frame completo do vídeo original, sem anotações, extraído e verificado contra o recorte",
            "guardado na análise (coluna \"extracao\" do manifesto). Frames com várias deteções aparecem uma só vez.",
            "Frames guardados no leitor (tipo \"frame guardado\"): frame original no instante escolhido pelo analista.",
            "Vídeos com píxeis não quadrados (coluna \"proporcao\"): o frame foi redimensionado do tamanho gravado",
            "para o de apresentação, como na análise; o frame exato pode ser obtido do vídeo pelo n.º de frame.",
            "",
            f"Deteções: {len(validated.items)} · frames: {frames} · frames guardados: {len(validated.frames)} · "
            f"falhas: {len(failures)}",
            "O manifesto.csv tem uma linha por deteção, com o SHA-256 de cada ficheiro.",
        ]
        if failures:
            readme += ["", "Frames que não foi possível extrair:", *failures]
        zf.writestr("LEIAME.txt", "\n".join(readme) + "\n")
    yield sink.take()
