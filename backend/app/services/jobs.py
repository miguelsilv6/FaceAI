"""Execução das análises em segundo plano (uma de cada vez), com progresso e cancelamento."""

from __future__ import annotations

import logging
import math
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine as get_db_engine
from ..models import Detection, Job, JobStatus, ReferenceImage, Video, utcnow
from .audit import log_event
from .face_engine import get_engine
from .imageio import write_jpeg
from .matcher import ReferenceSet, is_match
from .references import reference_embeddings
from .video_processor import annotate, crop_with_margin, format_timestamp, sample_frames

log = logging.getLogger(__name__)

PROGRESS_COMMIT_EVERY = 10  # frames


class JobCancelled(Exception):
    pass


class JobRunner:
    def __init__(self) -> None:
        self._executor: ThreadPoolExecutor | None = None
        self._cancel: dict[int, threading.Event] = {}

    def submit(self, job_id: int) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="faceai-job")
        self._cancel[job_id] = threading.Event()
        self._executor.submit(self._run_safe, job_id)

    def cancel(self, job_id: int) -> bool:
        ev = self._cancel.get(job_id)
        if ev is None:
            return False
        ev.set()
        return True

    def shutdown(self) -> None:
        for ev in self._cancel.values():
            ev.set()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _run_safe(self, job_id: int) -> None:
        try:
            run_job(job_id, self._cancel.get(job_id) or threading.Event())
        except Exception:  # pragma: no cover - registado no próprio job
            log.exception("Falha inesperada no job %s", job_id)
        finally:
            self._cancel.pop(job_id, None)


runner = JobRunner()


def job_dir(job: Job) -> Path:
    return settings.case_dir(job.case_id) / "jobs" / f"job_{job.id:05d}"


def recover_interrupted_jobs() -> None:
    """Marca como falhadas as análises que estavam a correr quando o servidor parou."""
    with Session(get_db_engine(), expire_on_commit=False) as s:
        for job in s.exec(select(Job).where(Job.status.in_([JobStatus.running, JobStatus.queued]))):
            job.status = JobStatus.failed
            job.message = "Interrompida (o servidor foi reiniciado)."
            job.finished_at = utcnow()
            s.add(job)
            log_event(s, "job.interrupted", case_id=job.case_id, actor="sistema",
                      details={"job_id": job.id}, commit=False)
        s.commit()


def run_job(job_id: int, cancel: threading.Event) -> None:
    engine = get_engine()
    with Session(get_db_engine(), expire_on_commit=False) as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        if cancel.is_set():
            _finish(s, job, JobStatus.cancelled, "Cancelada antes de iniciar.")
            return
        p = job.params
        job.status = JobStatus.running
        job.started_at = utcnow()
        job.environment = engine.environment()
        s.add(job)
        s.commit()
        log_event(s, "job.started", case_id=job.case_id, actor=job.created_by,
                  details={"job_id": job.id, "params": p, "environment": job.environment})

        try:
            refs = [s.get(ReferenceImage, rid) for rid in job.reference_ids]
            refs = [r for r in refs if r is not None]
            used_refs, ref_matrix, warnings = reference_embeddings(
                refs, engine, model=p["model"], detector=p["detector"]
            )
            if not used_refs:
                raise ValueError("Nenhuma imagem de referência válida. " + " ".join(warnings))
            reference_set = ReferenceSet(ref_matrix)
            job.reference_ids = [r.id for r in used_refs]
            job.message = " ".join(warnings)

            videos = [v for v in (s.get(Video, vid) for vid in job.video_ids) if v is not None]
            if not videos:
                raise ValueError("Nenhum vídeo selecionado.")

            threshold = float(p["threshold"])
            candidate_threshold = threshold + float(p.get("candidate_margin", 0.0))
            min_size = int(p.get("min_face_size", 0))
            min_conf = float(p.get("min_confidence", 0.0))
            upscale = float(p.get("upscale", 1.0))
            sample_fps = float(p["sample_fps"])
            out_dir = job_dir(job)

            def frames_for(v: Video) -> int:
                step = max(1, int(round(v.fps / sample_fps))) if v.fps else 1
                return max(1, math.ceil(v.frame_count / step)) if v.frame_count else 1

            total = sum(frames_for(v) for v in videos)
            done = 0

            for video in videos:
                job.current_video_id = video.id
                for frame in sample_frames(video.stored_path, sample_fps, fps=video.fps or None):
                    if cancel.is_set():
                        raise JobCancelled()
                    faces = engine.detect(frame.image, model=p["model"], detector=p["detector"], upscale=upscale)
                    for k, face in enumerate(faces):
                        if min(face.w, face.h) < min_size or face.confidence < min_conf:
                            continue
                        job.faces_detected += 1
                        result = reference_set.best_match(face.embedding)
                        if result.distance > candidate_threshold:
                            continue
                        matched = is_match(result.distance, threshold)
                        ref = used_refs[result.reference_index]
                        stem = f"v{video.id:04d}_f{frame.index:07d}_{k}"
                        crop_path = out_dir / "crops" / f"{stem}.jpg"
                        frame_path = out_dir / "frames" / f"{stem}.jpg"
                        write_jpeg(crop_path, crop_with_margin(frame.image, face.x, face.y, face.w, face.h))
                        label = f"{format_timestamp(frame.timestamp_s)} sim={result.similarity:.2f}"
                        color = (0, 0, 255) if matched else (0, 165, 255)
                        write_jpeg(frame_path, annotate(frame.image, face.x, face.y, face.w, face.h, label, color))
                        s.add(Detection(
                            job_id=job.id, video_id=video.id, reference_id=ref.id,
                            frame_index=frame.index, timestamp_s=round(frame.timestamp_s, 3),
                            x=face.x, y=face.y, w=face.w, h=face.h,
                            face_confidence=round(face.confidence, 4),
                            distance=round(result.distance, 4), similarity=round(result.similarity, 4),
                            is_match=matched, crop_path=str(crop_path), frame_path=str(frame_path),
                        ))
                        if matched:
                            job.matches += 1
                    done += 1
                    job.frames_processed += 1
                    if done % PROGRESS_COMMIT_EVERY == 0:
                        job.progress = min(0.999, done / total)
                        s.add(job)
                        s.commit()
            job.progress = 1.0
            _finish(s, job, JobStatus.completed,
                    (job.message + " " if job.message else "")
                    + f"{job.matches} correspondência(s) em {job.frames_processed} frames analisados.")
        except JobCancelled:
            _finish(s, job, JobStatus.cancelled, "Cancelada pelo utilizador.")
        except Exception as exc:
            log.error("Job %s falhou: %s", job_id, traceback.format_exc())
            _finish(s, job, JobStatus.failed, f"Erro: {exc}")


def _finish(s: Session, job: Job, status: JobStatus, message: str) -> None:
    job.status = status
    job.message = message.strip()
    job.finished_at = utcnow()
    job.current_video_id = None
    s.add(job)
    s.commit()
    log_event(s, f"job.{status.value}", case_id=job.case_id, actor=job.created_by or "sistema",
              details={"job_id": job.id, "message": job.message, "matches": job.matches,
                       "frames_processed": job.frames_processed})
