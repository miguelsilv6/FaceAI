"""Exportação de resultados: relatório HTML/PDF, CSV, JSON e ZIP com recortes."""

from __future__ import annotations

import base64
import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session, select

from .. import __version__
from ..config import settings
from ..models import AuditEvent, Case, Detection, Job, ReferenceImage, ReviewStatus, Track, Video
from .clock import format_real, real_time
from .locations import locations_by_id, video_place
from .persons import person_segments, summarize_persons
from .segments import Interval, merge_intervals
from .video_processor import format_timestamp

_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)
_TEMPLATES.filters["ts"] = format_timestamp

CSV_FIELDS = [
    "detection_id", "video_id", "video_filename", "location_name", "camera_name", "frame_index",
    "timestamp_s", "timestamp", "real_time", "kind", "evidence", "x", "y", "w", "h", "face_confidence", "reference_id",
    "reference_filename",
    "distance", "similarity", "is_match", "track_id", "track_is_match", "person_id", "person_label", "quality",
    "sharpness", "size_score", "frontal", "colors", "review_status", "reviewed_by", "reviewed_at", "review_note",
    "crop_file", "frame_file",
]


def match_segments(detections: list[Detection], gap_s: float,
                   tracks: dict[int, Track] | None = None) -> list[dict[str, Any]]:
    """Segmentos de aparição a partir das correspondências não rejeitadas na revisão.

    Conta como correspondência uma deteção abaixo do limiar ou que pertença a um track com correspondência; cada
    track com correspondência forma um único intervalo.
    """
    tracks = tracks or {}
    intervals: list[Interval] = []
    by_track: dict[int, list[Detection]] = {}
    last_saved: dict[int, float] = {}
    for d in detections:
        if d.track_id is not None:
            last_saved[d.track_id] = max(last_saved.get(d.track_id, d.timestamp_s), d.timestamp_s)
        if d.review_status == ReviewStatus.rejected:
            continue
        track = tracks.get(d.track_id) if d.track_id is not None else None
        if track is not None and track.is_match and track.review_status != ReviewStatus.rejected:
            by_track.setdefault(track.id, []).append(d)
        elif d.is_match:
            intervals.append(Interval(d.video_id, d.timestamp_s, d.timestamp_s, d.distance, d.id, (d.id,)))
    for tid, dets in by_track.items():
        # Do primeiro ao último frame guardado (com evidência visual) e não rejeitado do track, como um só bloco:
        # frames-chave espaçados (filtro de movimento) não partem o segmento de alguém parado.
        # Se o último frame guardado do track não foi rejeitado, o fim estende-se ao fim do track (frames
        # estáticos saltados pelo filtro de movimento em que a pessoa continuava presente).
        t = tracks[tid]
        best = min(dets, key=lambda d: d.distance)
        times = [d.timestamp_s for d in dets]
        end = max(times)
        if end >= last_saved.get(tid, end):
            end = max(end, t.end_s)
        body = getattr(t, "kind", "face") == "body"
        evidence = ("vestuário",) if body else ("face",)
        if body:
            # A distância ReID não é comparável à facial: o "melhor" frame do segmento continua a ser o facial.
            intervals.append(Interval(t.video_id, min(times), end, 2.0, best.id, tuple(d.id for d in dets), evidence))
            continue
        intervals.append(Interval(t.video_id, min(times), end, best.distance, best.id, tuple(d.id for d in dets),
                                  evidence))
    return [seg.to_dict() for seg in merge_intervals(intervals, max_gap_s=gap_s)]


def load_job_data(s: Session, job: Job) -> dict[str, Any]:
    case = s.get(Case, job.case_id)
    videos = {v.id: v for v in s.exec(select(Video).where(Video.id.in_(job.video_ids or [-1])))}
    refs = {r.id: r for r in s.exec(select(ReferenceImage).where(ReferenceImage.id.in_(job.reference_ids or [-1])))}
    detections = list(
        s.exec(select(Detection).where(Detection.job_id == job.id).order_by(Detection.video_id, Detection.timestamp_s))
    )
    tracks = {t.id: t for t in s.exec(select(Track).where(Track.job_id == job.id).order_by(Track.video_id,
                                                                                           Track.start_s))}
    audit = list(s.exec(select(AuditEvent).where(AuditEvent.case_id == job.case_id).order_by(AuditEvent.id)))
    return {"case": case, "job": job, "videos": videos, "references": refs, "detections": detections,
            "tracks": tracks, "audit": audit, "locations": locations_by_id(s, job.case_id)}


def place_of(v: Video | None, data: dict[str, Any]) -> dict[str, Any]:
    """Local e câmara de um vídeo ("Local · Câmara")."""
    if v is None:
        return {"location_id": None, "location_name": "", "camera_name": "", "camera_label": ""}
    return video_place(v, data.get("locations") or {})


def video_real_time(v: Video | None, video_s: float) -> str:
    """Hora real (relógio do DVR + desvio) de um instante do vídeo, ou "" se o vídeo não tiver a hora acertada."""
    if v is None:
        return ""
    return format_real(real_time(v.dvr_start, v.dvr_rate, v.clock_offset_s, video_s))


_TEMPLATES.globals["real_at"] = lambda v, t: video_real_time(v, t)


def is_discover(job: Job) -> bool:
    return job.params.get("mode") == "discover"


def segments_for(data: dict[str, Any]) -> list[dict[str, Any]]:
    gap = data["job"].params.get("segment_gap_s", 2.0)
    if is_discover(data["job"]):
        segs = person_segments(list(data.get("tracks", {}).values()), None, gap)
    else:
        segs = match_segments(data["detections"], gap, data.get("tracks"))
    for seg in segs:
        v = data["videos"].get(seg["video_id"])
        seg["real_start"] = video_real_time(v, seg["start_s"])
        seg["real_end"] = video_real_time(v, seg["end_s"])
        seg.update(place_of(v, data))
    return segs


def effective_match(d: Detection, data: dict[str, Any]) -> bool:
    t = data.get("tracks", {}).get(d.track_id) if d.track_id is not None else None
    return d.is_match or bool(t and t.is_match)


def _person_of(d: Detection, t) -> int | None:
    return d.person_id or (t.person_id if t is not None else None)


def person_label(job, person_id: int | None) -> str:
    """Nome da pessoa procurada (várias pessoas por análise); vazio no modo descoberta ou com uma só pessoa."""
    if person_id is None or is_discover(job):
        return ""
    persons = (job.params or {}).get("persons") or []
    return next((p["label"] for p in persons if p["id"] == person_id), "") if len(persons) > 1 else ""


def detection_row(d: Detection, data: dict[str, Any]) -> dict[str, Any]:
    v, r = data["videos"].get(d.video_id), data["references"].get(d.reference_id)
    t = data.get("tracks", {}).get(d.track_id) if d.track_id is not None else None
    return {
        "detection_id": d.id, "video_id": d.video_id, "video_filename": v.filename if v else "",
        "location_name": place_of(v, data)["location_name"], "camera_name": place_of(v, data)["camera_name"],
        "frame_index": d.frame_index, "timestamp_s": d.timestamp_s, "timestamp": format_timestamp(d.timestamp_s),
        "real_time": video_real_time(v, d.timestamp_s),
        "kind": d.kind or "face", "evidence": (t.evidence if t is not None and t.kind == "body" else "face"),
        "x": d.x, "y": d.y, "w": d.w, "h": d.h, "face_confidence": d.face_confidence,
        "reference_id": d.reference_id, "reference_filename": r.filename if r else "",
        "distance": d.distance, "similarity": d.similarity, "is_match": d.is_match,
        "track_id": d.track_id if d.track_id is not None else "",
        "track_is_match": bool(t and t.is_match),
        "person_id": _person_of(d, t) or "", "person_label": person_label(data["job"], _person_of(d, t)),
        "quality": d.quality, "sharpness": d.sharpness,
        "size_score": d.size_score, "frontal": "" if d.frontal is None else d.frontal,
        "colors": "; ".join(f"{k}: {v}" for k, v in (d.colors or {}).items()),
        "review_status": d.review_status.value, "reviewed_by": d.reviewed_by,
        "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else "", "review_note": d.review_note,
        "crop_file": f"crops/{Path(d.crop_path).name}", "frame_file": f"frames/{Path(d.frame_path).name}",
    }


def to_csv(data: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for d in data["detections"]:
        writer.writerow(detection_row(d, data))
    return buf.getvalue()


def to_json(data: dict[str, Any]) -> str:
    job: Job = data["job"]
    case: Case = data["case"]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "faceai_version": __version__,
        "case": case.model_dump(mode="json") if case else None,
        "job": job.model_dump(mode="json"),
        "locations": (job.params or {}).get("locations") or [],
        "videos": [
            {**v.model_dump(mode="json", exclude={"stored_path", "playback_path"}), **place_of(v, data)}
            for v in data["videos"].values()
        ],
        "references": [
            r.model_dump(mode="json", exclude={"stored_path", "face_crop_path"}) for r in data["references"].values()
        ],
        "segments": segments_for(data),
        "persons": persons_for(data),
        "tracks": [{**t.model_dump(mode="json"),
                    "real_start": video_real_time(data["videos"].get(t.video_id), t.start_s),
                    "real_end": video_real_time(data["videos"].get(t.video_id), t.end_s)}
                   for t in data.get("tracks", {}).values()],
        "detections": [detection_row(d, data) for d in data["detections"]],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def persons_for(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Modo descoberta: resumo de cada pessoa com as aparições (hora no vídeo e hora real)."""
    if not is_discover(data["job"]):
        return []
    tracks = list(data.get("tracks", {}).values())
    gap = data["job"].params.get("segment_gap_s", 2.0)
    persons = summarize_persons(tracks)
    for person in persons:
        person["appearances"] = []
        for seg in person_segments(tracks, person["person_id"], gap):
            v = data["videos"].get(seg["video_id"])
            person["appearances"].append({
                "video_id": seg["video_id"], "video_filename": v.filename if v else "", **place_of(v, data),
                "start_s": seg["start_s"], "end_s": seg["end_s"], "duration_s": seg["duration_s"],
                "real_start": video_real_time(v, seg["start_s"]), "real_end": video_real_time(v, seg["end_s"]),
            })
    return persons


def audit_jsonl(data: dict[str, Any]) -> str:
    return "".join(
        json.dumps(e.model_dump(mode="json"), ensure_ascii=False) + "\n" for e in data["audit"]
    )


def _img_data_uri(path: str | None) -> str:
    if not path or not Path(path).exists():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def clothing_for(data: dict[str, Any], by_id: dict[int, Detection]) -> list[dict[str, Any]]:
    """Tracks de corpo (amostras e correspondências só pelo vestuário), por vídeo e hora."""
    out = []
    tracks = data.get("tracks", {})
    for t in tracks.values():
        if t.kind != "body":
            continue
        best = by_id.get(t.best_detection_id)
        tv = data["videos"].get(t.video_id)
        out.append({"t": t, "video": tv, "real_start": video_real_time(tv, t.start_s),
                    "real_end": video_real_time(tv, t.end_s),
                    "crop_img": _img_data_uri(best.crop_path if best else None),
                    "frame_img": _img_data_uri(best.frame_path if best else None) if t.evidence == "vestuário" else ""})
    return sorted(out, key=lambda x: (x["t"].evidence != "face+vestuário", x["t"].video_id, x["t"].start_s))


def render_html(data: dict[str, Any], *, max_images: int = 300) -> str:
    job: Job = data["job"]
    detections: list[Detection] = data["detections"]
    by_id = {d.id: d for d in detections}
    segments = segments_for(data)
    for seg in segments:
        best = by_id.get(seg["best_detection_id"])
        seg["frame_img"] = _img_data_uri(best.frame_path if best else None)
        seg["crop_img"] = _img_data_uri(best.crop_path if best else None)
        seg["video"] = data["videos"].get(seg["video_id"])
    discover = is_discover(job)
    face_dets = [d for d in detections if (d.kind or "face") == "face"]
    # No modo descoberta todas as faces guardadas são resultado; no modo de procura, só as correspondências.
    matches = face_dets if discover else [d for d in face_dets if effective_match(d, data)]
    rows = []
    for i, d in enumerate(matches):
        row = detection_row(d, data)
        row["crop_img"] = _img_data_uri(d.crop_path) if i < max_images else ""
        rows.append(row)
    persons = persons_for(data)
    for person in persons:
        best = by_id.get(person["best_detection_id"])
        person["crop_img"] = _img_data_uri(best.crop_path if best else None)
        person["frame_img"] = _img_data_uri(best.frame_path if best else None)
    clothing = clothing_for(data, by_id)
    tracks = []
    for t in data.get("tracks", {}).values():
        if t.kind == "body" or (not t.is_match and not discover):
            continue
        best = by_id.get(t.best_detection_id)
        tv = data["videos"].get(t.video_id)
        tracks.append({"t": t, "video": tv, "real_start": video_real_time(tv, t.start_s),
                       "real_end": video_real_time(tv, t.end_s),
                       "ref": data["references"].get(t.reference_id),
                       "crop_img": _img_data_uri(best.crop_path if best else None)})
    references = [
        {"ref": r, "img": _img_data_uri(r.face_crop_path or r.stored_path)} for r in data["references"].values()
    ]
    review_counts = {s.value: sum(1 for d in matches if d.review_status == s) for s in ReviewStatus}
    return _TEMPLATES.get_template("report.html.j2").render(
        generated_at=datetime.now(UTC),
        version=__version__,
        case=data["case"],
        job=job,
        videos=sorted(data["videos"].values(),
                      key=lambda v: (place_of(v, data)["location_name"].casefold(), v.id)),
        place=lambda v: place_of(v, data),
        references=references,
        segments=segments,
        rows=rows,
        tracks=tracks,
        candidates=0 if discover else sum(1 for d in face_dets if not effective_match(d, data)),
        clothing=clothing,
        discover=discover,
        persons=persons,
        review_counts=review_counts,
        audit=data["audit"],
        max_images=max_images,
    )


def render_pdf(html: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=html, base_url=str(settings.data_dir)).write_pdf()


def build_zip(data: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    html = render_html(data)
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("relatorio.html", html)
        zf.writestr("deteccoes.csv", to_csv(data))
        zf.writestr("resultados.json", to_json(data))
        zf.writestr("auditoria.jsonl", audit_jsonl(data))
        for d in data["detections"]:
            for p, folder in ((d.crop_path, "crops"), (d.frame_path, "frames")):
                if p and Path(p).exists():
                    zf.write(p, f"{folder}/{Path(p).name}")
        for r in data["references"].values():
            if Path(r.stored_path).exists():
                zf.write(r.stored_path, f"referencias/{r.id:05d}_{Path(r.filename).name}")
    return buf.getvalue()
