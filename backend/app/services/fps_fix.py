"""Correção do fps de um vídeo extraído de um formato proprietário (.sec).

Os blocos .sec não trazem o instante de cada frame e muitos DVR não indicam o fps no SPS: a extração assume 25 fps
e, se o DVR gravou a 12, o vídeo reproduz-se ~2× mais depressa e os tempos (duração, deteções, tracks) ficam
encurtados na mesma proporção. Os frames são os mesmos; só o relógio do vídeo muda. A correção:

- refaz o MP4 de análise com o novo ritmo (mesma sequência H.264, sem recodificação: o SHA-256 H.264 é confirmado);
- recalcula os tempos das análises que usam o vídeo (deteção/frame guardado = n.º do frame ÷ fps; tracks × k);
- acerta a hora real (_rescale_clock): com OCR mantém a hora de cada frame; com ponto manual mantém o ponto;
- refaz a cópia de reprodução e fica na auditoria (video.fps_corrected), com o histórico no próprio vídeo.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import Session, select

from ..models import Detection, Job, JobStatus, SavedFrame, Track, Video, utcnow
from . import sec_extract
from .audit import log_event
from .transcode import restart_copy

MIN_FPS, MAX_FPS = 0.5, 60.0
SUGGEST_TOLERANCE = 0.03  # taxa do relógio do DVR a mais de 3% de 1 → o fps está errado


def jobs_using(s: Session, video: Video) -> list[Job]:
    return [j for j in s.exec(select(Job).where(Job.case_id == video.case_id).order_by(Job.id))
            if video.id in (j.video_ids or [])]


def correct_fps(s: Session, video: Video, new_fps: float, *, reason: str, actor: str,
                source: str = "manual") -> Video:
    """Valida e põe na fila de extração a cópia com o novo fps; a correção é aplicada quando ela estiver pronta
    (apply_fps_correction). Num .sec grande, refazer o MP4 demora mais do que o tempo de um pedido."""
    if not video.extraction or not video.analysis_path or not sec_extract.is_sec(video):
        raise HTTPException(422, "Só o fps de vídeos extraídos de formatos proprietários (.sec) pode ser corrigido.")
    if not sec_extract.is_ready(video) or video.extraction_status != sec_extract.READY:
        raise HTTPException(409, "O vídeo ainda está a ser extraído: aguarde que termine.")
    if not MIN_FPS <= new_fps <= MAX_FPS:
        raise HTTPException(422, f"fps inválido: tem de estar entre {MIN_FPS:g} e {MAX_FPS:g}.")
    if any(j.status in (JobStatus.queued, JobStatus.running) for j in jobs_using(s, video)):
        raise HTTPException(409, "Há uma análise em fila ou em curso com este vídeo: aguarde que termine.")
    if abs(new_fps - video.fps) < 1e-6:
        raise HTTPException(422, f"O vídeo já está a {video.fps:g} fps.")
    label = "relógio do DVR (OCR)" if source == "ocr" else "manual"
    request = {"fps": new_fps, "from": video.fps, "label": label, "reason": reason, "actor": actor,
               "requested_at": utcnow().isoformat(timespec="seconds")}
    log_event(s, "video.fps_correction_requested", case_id=video.case_id, actor=actor,
              details={"video_id": video.id, "filename": video.filename, "from_fps": video.fps, "to_fps": new_fps,
                       "source": label, "reason": reason})
    sec_extract.request_extraction(s, video, correction=request)
    s.refresh(video)
    return video


def apply_fps_correction(s: Session, video: Video, analysis: Path, info: dict[str, Any],
                         req: dict[str, Any]) -> None:
    """Chamada pela fila quando a cópia com o novo fps está feita: troca a cópia, recalcula os tempos das análises
    e acerta o relógio, numa transação."""
    previous = {k: v for k, v in (video.extraction or {}).items() if k != "pending_fps"}
    old_fps, new_fps, actor = video.fps, req["fps"], req["actor"]
    if info["h264_sha256"] != previous.get("h264_sha256"):  # o original não mudou: a sequência tem de ser igual
        if str(analysis) != video.analysis_path:
            analysis.unlink(missing_ok=True)
        video.extraction = previous
        video.extraction_status, video.extraction_progress = sec_extract.READY, 1.0
        video.extraction_error = ("a correção do fps foi recusada: a sequência H.264 extraída não coincide com a da "
                                  "importação")
        s.add(video)
        s.commit()
        log_event(s, "video.fps_correction_failed", case_id=video.case_id, actor="sistema",
                  details={"video_id": video.id, "error": video.extraction_error,
                           "expected": previous.get("h264_sha256"), "got": info["h264_sha256"]})
        return
    jobs = jobs_using(s, video)
    old_sha = video.analysis_sha256
    k = old_fps / new_fps
    history = [*previous.get("fps_history", []),
               {"from": old_fps, "to": new_fps, "source": req["label"], "reason": req["reason"], "by": actor,
                "at": utcnow().isoformat(timespec="seconds")}]
    if video.analysis_path and video.analysis_path != str(analysis):
        Path(video.analysis_path).unlink(missing_ok=True)
    video.extraction = {**info, "fps_history": history}
    video.analysis_path, video.analysis_sha256 = str(analysis), info["analysis_sha256"]
    video.fps, video.duration_s = round(new_fps, 3), round(video.frame_count / new_fps, 3)
    video.extraction_status, video.extraction_progress, video.extraction_error = sec_extract.READY, 1.0, ""
    _rescale_clock(video, k)

    job_ids = [j.id for j in jobs] or [-1]
    n_det = s.exec(update(Detection).where(Detection.video_id == video.id, Detection.job_id.in_(job_ids))
                   .values(timestamp_s=Detection.frame_index / new_fps)).rowcount
    n_frames = s.exec(update(SavedFrame).where(SavedFrame.video_id == video.id)
                      .values(timestamp_s=SavedFrame.frame_index / new_fps)).rowcount
    n_tracks = s.exec(update(Track).where(Track.video_id == video.id, Track.job_id.in_(job_ids))
                      .values(start_s=Track.start_s * k, end_s=Track.end_s * k)).rowcount
    s.add(video)
    s.commit()
    s.refresh(video)
    log_event(s, "video.fps_corrected", case_id=video.case_id, actor=actor,
              details={"video_id": video.id, "filename": video.filename, "from_fps": old_fps, "to_fps": new_fps,
                       "source": req["label"], "reason": req["reason"], "duration_s": video.duration_s,
                       "h264_sha256": info["h264_sha256"], "previous_analysis_sha256": old_sha,
                       "analysis_sha256": video.analysis_sha256, "dvr_rate": video.dvr_rate,
                       "jobs": [j.id for j in jobs], "detections_retimed": n_det, "tracks_retimed": n_tracks,
                       "saved_frames_retimed": n_frames,
                       "note": "os frames analisados não mudam; só os tempos (n.º do frame ÷ fps) são recalculados"})
    restart_copy(s, video, actor=actor)


def _rescale_clock(video: Video, k: float) -> None:
    """Os tempos do vídeo passam a t × k.

    OCR: a taxa foi ajustada às leituras do relógio, frame a frame; a hora real de cada frame mantém-se com
    taxa / k (fica ≈ 1 quando o novo fps veio da sugestão do relógio). Ponto manual: a hora indicada continua no
    mesmo frame, e a partir dele o relógio avança ao ritmo certo (taxa 1)."""
    cal: dict[str, Any] = dict(video.time_calibration or {})
    if video.time_source == "manual" and "video_s" in cal and video.dvr_start:
        old_s = cal["video_s"]
        cal["video_s"] = round(old_s * k, 3)
        start = datetime.fromisoformat(video.dvr_start) + timedelta(seconds=old_s - cal["video_s"])
        video.dvr_start = start.isoformat(timespec="milliseconds")
    else:
        video.dvr_rate = round(video.dvr_rate / k, 6)
        if cal.get("samples"):
            cal["samples"] = [{**smp, "video_s": round(smp["video_s"] * k, 3)} if "video_s" in smp else smp
                              for smp in cal["samples"]]
        if "rate" in cal:
            cal["rate"] = video.dvr_rate
    video.time_calibration = cal


def suggest_from_clock(video: Video, rate: float) -> None:
    """Depois de calibrar o relógio por OCR: se a taxa do DVR se afasta de 1, o fps do vídeo extraído está errado e
    o real é fps ÷ taxa. Só sugere (o analista confirma); uma taxa perto de 1 apaga a sugestão anterior."""
    if not video.extraction or not rate:
        return
    x = {k: v for k, v in video.extraction.items() if k != "suggested_fps"}
    if abs(rate - 1) > SUGGEST_TOLERANCE:
        fps = round(video.fps / rate, 2)
        if MIN_FPS <= fps <= MAX_FPS:
            x["suggested_fps"] = {"fps": fps, "rate": rate, "at": utcnow().isoformat(timespec="seconds")}
    video.extraction = x
