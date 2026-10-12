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
from ..models import AuditEvent, Case, Detection, Job, ReferenceImage, ReviewStatus, Video
from .segments import build_segments
from .video_processor import format_timestamp

_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)
_TEMPLATES.filters["ts"] = format_timestamp

CSV_FIELDS = [
    "detection_id", "video_id", "video_filename", "frame_index", "timestamp_s", "timestamp",
    "x", "y", "w", "h", "face_confidence", "reference_id", "reference_filename",
    "distance", "similarity", "is_match", "review_status", "reviewed_by", "reviewed_at",
    "review_note", "crop_file", "frame_file",
]


def match_segments(detections: list[Detection], gap_s: float) -> list[dict[str, Any]]:
    """Segmentos de aparição a partir das correspondências não rejeitadas na revisão."""
    kept = [d for d in detections if d.is_match and d.review_status != ReviewStatus.rejected]
    return [seg.to_dict() for seg in build_segments(kept, max_gap_s=gap_s)]


def load_job_data(s: Session, job: Job) -> dict[str, Any]:
    case = s.get(Case, job.case_id)
    videos = {v.id: v for v in s.exec(select(Video).where(Video.id.in_(job.video_ids or [-1])))}
    refs = {r.id: r for r in s.exec(select(ReferenceImage).where(ReferenceImage.id.in_(job.reference_ids or [-1])))}
    detections = list(
        s.exec(select(Detection).where(Detection.job_id == job.id).order_by(Detection.video_id, Detection.timestamp_s))
    )
    audit = list(s.exec(select(AuditEvent).where(AuditEvent.case_id == job.case_id).order_by(AuditEvent.id)))
    return {"case": case, "job": job, "videos": videos, "references": refs, "detections": detections, "audit": audit}


def detection_row(d: Detection, data: dict[str, Any]) -> dict[str, Any]:
    v, r = data["videos"].get(d.video_id), data["references"].get(d.reference_id)
    return {
        "detection_id": d.id, "video_id": d.video_id, "video_filename": v.filename if v else "",
        "frame_index": d.frame_index, "timestamp_s": d.timestamp_s, "timestamp": format_timestamp(d.timestamp_s),
        "x": d.x, "y": d.y, "w": d.w, "h": d.h, "face_confidence": d.face_confidence,
        "reference_id": d.reference_id, "reference_filename": r.filename if r else "",
        "distance": d.distance, "similarity": d.similarity, "is_match": d.is_match,
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
        "videos": [
            v.model_dump(mode="json", exclude={"stored_path", "playback_path"}) for v in data["videos"].values()
        ],
        "references": [
            r.model_dump(mode="json", exclude={"stored_path", "face_crop_path"}) for r in data["references"].values()
        ],
        "segments": match_segments(data["detections"], job.params.get("segment_gap_s", 2.0)),
        "detections": [detection_row(d, data) for d in data["detections"]],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def audit_jsonl(data: dict[str, Any]) -> str:
    return "".join(
        json.dumps(e.model_dump(mode="json"), ensure_ascii=False) + "\n" for e in data["audit"]
    )


def _img_data_uri(path: str | None) -> str:
    if not path or not Path(path).exists():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def render_html(data: dict[str, Any], *, max_images: int = 300) -> str:
    job: Job = data["job"]
    detections: list[Detection] = data["detections"]
    by_id = {d.id: d for d in detections}
    segments = match_segments(detections, job.params.get("segment_gap_s", 2.0))
    for seg in segments:
        best = by_id.get(seg["best_detection_id"])
        seg["frame_img"] = _img_data_uri(best.frame_path if best else None)
        seg["crop_img"] = _img_data_uri(best.crop_path if best else None)
        seg["video"] = data["videos"].get(seg["video_id"])
    matches = [d for d in detections if d.is_match]
    rows = []
    for i, d in enumerate(matches):
        row = detection_row(d, data)
        row["crop_img"] = _img_data_uri(d.crop_path) if i < max_images else ""
        rows.append(row)
    references = [
        {"ref": r, "img": _img_data_uri(r.face_crop_path or r.stored_path)} for r in data["references"].values()
    ]
    review_counts = {s.value: sum(1 for d in matches if d.review_status == s) for s in ReviewStatus}
    return _TEMPLATES.get_template("report.html.j2").render(
        generated_at=datetime.now(UTC),
        version=__version__,
        case=data["case"],
        job=job,
        videos=list(data["videos"].values()),
        references=references,
        segments=segments,
        rows=rows,
        candidates=sum(1 for d in detections if not d.is_match),
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
