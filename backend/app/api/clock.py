"""Hora real dos vídeos: calibração por OCR da data/hora impressa, ponto manual e desvio do relógio do DVR."""

from __future__ import annotations

from datetime import timedelta

import cv2
from fastapi import APIRouter, Depends, HTTPException, Response

from ..models import Video
from ..schemas import ClockManual, ClockOcr, ClockOcrTest, ClockOffset
from ..services import ocr
from ..services.audit import log_event
from .deps import ActorDep, SessionDep, authorize_objects

# Todas as rotas exigem sessão; os objetos do caminho têm de pertencer a um caso acessível (ver deps).
router = APIRouter(prefix="/api", tags=["hora real"], dependencies=[Depends(authorize_objects)])


def _video(session, video_id: int) -> Video:
    v = session.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "Vídeo não encontrado.")
    return v


def _clock_details(v: Video) -> dict:
    return {"video_id": v.id, "time_source": v.time_source, "dvr_start": v.dvr_start, "dvr_rate": v.dvr_rate,
            "clock_offset_s": v.clock_offset_s, "note": v.time_note}


@router.get("/videos/{video_id}/frame")
def video_frame(video_id: int, session: SessionDep, t: float = 0.0) -> Response:
    v = _video(session, video_id)
    try:
        frame = ocr.read_frame(v.source_path, max(0.0, min(t, v.duration_s)), v.fps)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return Response(content=buf.tobytes(), media_type="image/jpeg")


@router.post("/videos/{video_id}/clock/ocr-test")
def clock_ocr_test(video_id: int, body: ClockOcrTest, session: SessionDep) -> dict:
    """Lê a data/hora num frame (zona indicada ou procura automática) — para testar antes de calibrar."""
    v = _video(session, video_id)
    try:
        frame = ocr.read_frame(v.source_path, max(0.0, min(body.t, v.duration_s)), v.fps)
        if body.roi is None:
            found = ocr.find_timestamp(frame, body.date_order)
            if not found:
                return {"roi": None, "text": "", "dvr": None, "auto": True}
            return {"roi": found["roi"], "text": found["text"], "dvr": found["dvr"].isoformat(), "auto": True}
        text, dvr = ocr.read_timestamp(frame, body.roi, body.date_order)
    except ocr.OcrUnavailable as exc:
        raise HTTPException(501, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"roi": body.roi, "text": text, "dvr": dvr.isoformat() if dvr else None, "auto": False}


@router.post("/videos/{video_id}/clock/ocr", response_model=Video)
def clock_ocr(video_id: int, body: ClockOcr, session: SessionDep, actor: ActorDep) -> Video:
    v = _video(session, video_id)
    try:
        cal = ocr.calibrate(v.source_path, v.fps, v.duration_s, roi=body.roi, samples=body.samples,
                            date_order=body.date_order)
    except ocr.OcrUnavailable as exc:
        raise HTTPException(501, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if "error" in cal:
        log_event(session, "video.clock_ocr_failed", case_id=v.case_id, actor=actor,
                  details={"video_id": v.id, "error": cal["error"], "samples": cal["samples"]})
        raise HTTPException(422, {"message": cal["error"], "calibration": cal})
    v.dvr_start = cal["dvr_start"]
    v.dvr_rate = cal["rate"]
    v.clock_offset_s = body.clock_offset_s
    v.time_source = "ocr"
    v.time_note = body.note
    v.time_calibration = cal
    session.add(v)
    session.commit()
    session.refresh(v)
    log_event(session, "video.clock_set", case_id=v.case_id, actor=actor,
              details={**_clock_details(v), "calibration": cal})
    return v


@router.put("/videos/{video_id}/clock", response_model=Video)
def clock_manual(video_id: int, body: ClockManual, session: SessionDep, actor: ActorDep) -> Video:
    v = _video(session, video_id)
    dvr_time = body.dvr_time.replace(tzinfo=None)  # hora "de parede" do DVR, sem fuso
    v.dvr_start = (dvr_time - timedelta(seconds=body.video_s)).isoformat(timespec="milliseconds")
    v.dvr_rate = 1.0
    v.clock_offset_s = body.clock_offset_s
    v.time_source = "manual"
    v.time_note = body.note
    v.time_calibration = {"video_s": body.video_s, "dvr_time": dvr_time.isoformat()}
    session.add(v)
    session.commit()
    session.refresh(v)
    log_event(session, "video.clock_set", case_id=v.case_id, actor=actor, details=_clock_details(v))
    return v


@router.patch("/videos/{video_id}/clock/offset", response_model=Video)
def clock_offset(video_id: int, body: ClockOffset, session: SessionDep, actor: ActorDep) -> Video:
    v = _video(session, video_id)
    previous = v.clock_offset_s
    v.clock_offset_s = body.clock_offset_s
    if body.note is not None:
        v.time_note = body.note
    session.add(v)
    session.commit()
    session.refresh(v)
    log_event(session, "video.clock_offset", case_id=v.case_id, actor=actor,
              details={**_clock_details(v), "previous_offset_s": previous})
    return v


@router.delete("/videos/{video_id}/clock", response_model=Video)
def clock_reset(video_id: int, session: SessionDep, actor: ActorDep) -> Video:
    v = _video(session, video_id)
    before = _clock_details(v)
    v.dvr_start, v.dvr_rate, v.clock_offset_s, v.time_source, v.time_note = None, 1.0, 0.0, "", ""
    v.time_calibration = {}
    session.add(v)
    session.commit()
    session.refresh(v)
    log_event(session, "video.clock_cleared", case_id=v.case_id, actor=actor, details=before)
    return v
