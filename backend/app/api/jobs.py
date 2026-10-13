"""Análises, resultados, revisão humana e exportações."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from sqlmodel import select

from ..models import Detection, Job, JobStatus, ReferenceImage, Track, Video, utcnow
from ..schemas import JobCreate, ReviewUpdate
from ..services import reports
from ..services.audit import log_event
from ..services.face_engine import default_threshold, get_engine
from ..services.jobs import runner
from .deps import ActorDep, SessionDep, get_case_or_404, get_job_or_404

router = APIRouter(prefix="/api", tags=["análises"])


@router.post("/cases/{case_id}/jobs", response_model=Job, status_code=201)
def create_job(case_id: int, body: JobCreate, session: SessionDep, actor: ActorDep) -> Job:
    get_case_or_404(session, case_id)
    params = body.params
    engine = get_engine()
    models = [m.name for m in engine.models()]
    params.model = params.model or models[0]
    params.detector = params.detector or engine.detectors()[0]
    if params.model not in models:
        raise HTTPException(422, f"Modelo desconhecido para o motor '{engine.name}': {params.model}")
    if params.detector not in engine.detectors():
        raise HTTPException(422, f"Detetor desconhecido para o motor '{engine.name}': {params.detector}")
    if params.cascade:
        params.fast_detector = params.fast_detector or engine.default_fast_detector()
        if params.fast_detector and params.fast_detector not in engine.detectors():
            raise HTTPException(422, f"Detetor rápido desconhecido: {params.fast_detector}")
    if params.threshold is None:
        params.threshold = default_threshold(params.model, engine)

    videos = list(session.exec(select(Video).where(Video.case_id == case_id)))
    refs = list(session.exec(select(ReferenceImage).where(ReferenceImage.case_id == case_id)))
    video_ids = [v.id for v in videos if body.video_ids is None or v.id in body.video_ids]
    ref_ids = [r.id for r in refs if r.valid and (body.reference_ids is None or r.id in body.reference_ids)]
    if not video_ids:
        raise HTTPException(422, "Selecione pelo menos um vídeo do caso.")
    if not ref_ids:
        raise HTTPException(422, "Não há imagens de referência válidas selecionadas.")

    job = Job(case_id=case_id, params=params.model_dump(), video_ids=video_ids, reference_ids=ref_ids,
              created_by=actor)
    session.add(job)
    session.commit()
    session.refresh(job)
    log_event(session, "job.created", case_id=case_id, actor=actor,
              details={"job_id": job.id, "params": job.params, "video_ids": video_ids, "reference_ids": ref_ids})
    runner.submit(job.id)
    return job


@router.get("/cases/{case_id}/jobs", response_model=list[Job])
def list_jobs(case_id: int, session: SessionDep) -> list[Job]:
    return list(session.exec(select(Job).where(Job.case_id == case_id).order_by(Job.id.desc())))


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: int, session: SessionDep) -> Job:
    return get_job_or_404(session, job_id)


@router.post("/jobs/{job_id}/cancel", response_model=Job)
def cancel_job(job_id: int, session: SessionDep, actor: ActorDep) -> Job:
    job = get_job_or_404(session, job_id)
    if job.status not in (JobStatus.queued, JobStatus.running):
        raise HTTPException(409, "A análise já terminou.")
    runner.cancel(job_id)
    log_event(session, "job.cancel_requested", case_id=job.case_id, actor=actor, details={"job_id": job_id})
    session.refresh(job)
    return job


@router.get("/jobs/{job_id}/detections", response_model=list[Detection])
def list_detections(
    job_id: int,
    session: SessionDep,
    video_id: int | None = None,
    only_matches: bool = False,
    min_similarity: float | None = None,
) -> list[Detection]:
    get_job_or_404(session, job_id)
    q = select(Detection).where(Detection.job_id == job_id)
    if video_id is not None:
        q = q.where(Detection.video_id == video_id)
    if only_matches:
        q = q.where(Detection.is_match == True)  # noqa: E712
    if min_similarity is not None:
        q = q.where(Detection.similarity >= min_similarity)
    return list(session.exec(q.order_by(Detection.video_id, Detection.timestamp_s)))


@router.get("/jobs/{job_id}/segments")
def list_segments(job_id: int, session: SessionDep, gap_s: float | None = None) -> list[dict]:
    job = get_job_or_404(session, job_id)
    dets = list(session.exec(select(Detection).where(Detection.job_id == job_id)))
    tracks = {t.id: t for t in session.exec(select(Track).where(Track.job_id == job_id))}
    return reports.match_segments(dets, gap_s if gap_s is not None else job.params.get("segment_gap_s", 2.0),
                                  tracks)


@router.get("/jobs/{job_id}/tracks", response_model=list[Track])
def list_tracks(job_id: int, session: SessionDep, video_id: int | None = None) -> list[Track]:
    get_job_or_404(session, job_id)
    q = select(Track).where(Track.job_id == job_id)
    if video_id is not None:
        q = q.where(Track.video_id == video_id)
    return list(session.exec(q.order_by(Track.video_id, Track.start_s)))


@router.patch("/tracks/{track_id}/review", response_model=Track)
def review_track(track_id: int, body: ReviewUpdate, session: SessionDep, actor: ActorDep) -> Track:
    """Revê um track inteiro: aplica a decisão ao track e a todas as suas deteções."""
    track = session.get(Track, track_id)
    if track is None:
        raise HTTPException(404, "Track não encontrado.")
    now = utcnow()
    previous = track.review_status
    track.review_status, track.review_note, track.reviewed_by, track.reviewed_at = body.status, body.note, actor, now
    dets = list(session.exec(select(Detection).where(Detection.track_id == track_id)))
    for det in dets:
        det.review_status, det.review_note, det.reviewed_by, det.reviewed_at = body.status, body.note, actor, now
        session.add(det)
    session.add(track)
    session.commit()
    session.refresh(track)
    job = session.get(Job, track.job_id)
    log_event(session, "track.reviewed", case_id=job.case_id if job else None, actor=actor,
              details={"job_id": track.job_id, "track_id": track_id, "from": previous.value,
                       "to": body.status.value, "note": body.note, "detections": [d.id for d in dets]})
    return track


@router.patch("/detections/{det_id}/review", response_model=Detection)
def review_detection(det_id: int, body: ReviewUpdate, session: SessionDep, actor: ActorDep) -> Detection:
    det = session.get(Detection, det_id)
    if det is None:
        raise HTTPException(404, "Deteção não encontrada.")
    previous = det.review_status
    det.review_status = body.status
    det.review_note = body.note
    det.reviewed_by = actor
    det.reviewed_at = utcnow()
    session.add(det)
    session.commit()
    session.refresh(det)
    job = session.get(Job, det.job_id)
    log_event(session, "detection.reviewed", case_id=job.case_id if job else None, actor=actor,
              details={"job_id": det.job_id, "detection_id": det.id, "from": previous.value,
                       "to": body.status.value, "note": body.note})
    return det


@router.get("/detections/{det_id}/{kind}")
def detection_image(det_id: int, kind: str, session: SessionDep) -> FileResponse:
    det = session.get(Detection, det_id)
    if det is None or kind not in ("crop", "frame"):
        raise HTTPException(404, "Imagem não encontrada.")
    return FileResponse(det.crop_path if kind == "crop" else det.frame_path, media_type="image/jpeg")


EXPORTS = {
    "html": ("text/html; charset=utf-8", "html"),
    "pdf": ("application/pdf", "pdf"),
    "csv": ("text/csv; charset=utf-8", "csv"),
    "json": ("application/json", "json"),
    "zip": ("application/zip", "zip"),
}


@router.get("/jobs/{job_id}/export/{fmt}")
def export_job(job_id: int, fmt: str, session: SessionDep, actor: ActorDep, analyst: str | None = None) -> Response:
    if fmt not in EXPORTS:
        raise HTTPException(404, f"Formato desconhecido: {fmt}")
    job = get_job_or_404(session, job_id)
    if job.status in (JobStatus.queued, JobStatus.running):
        raise HTTPException(409, "A análise ainda está a decorrer.")
    actor = actor or (analyst or "").strip()[:100]
    # O registo da exportação é feito antes de gerar o ficheiro, para que fique incluído na auditoria exportada.
    log_event(session, "job.exported", case_id=job.case_id, actor=actor, details={"job_id": job_id, "format": fmt})
    data = reports.load_job_data(session, job)
    if fmt == "html":
        body: bytes | str = reports.render_html(data)
    elif fmt == "pdf":
        body = reports.render_pdf(reports.render_html(data, max_images=150))
    elif fmt == "csv":
        body = "﻿" + reports.to_csv(data)  # BOM para abrir corretamente no Excel
    elif fmt == "json":
        body = reports.to_json(data)
    else:
        body = reports.build_zip(data)
    media_type, ext = EXPORTS[fmt]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"faceai_caso{job.case_id}_analise{job.id}_{stamp}.{ext}"
    return Response(content=body, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
