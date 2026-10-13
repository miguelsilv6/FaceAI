"""Execução das análises em segundo plano (uma de cada vez), com progresso e cancelamento."""

from __future__ import annotations

import bisect
import logging
import math
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine as get_db_engine
from ..models import Detection, Job, JobStatus, ReferenceImage, Track, Video, utcnow
from .audit import log_event
from .clustering import centroids_of, cluster_embeddings
from .face_engine import FaceEngine, get_engine
from .imageio import write_jpeg
from .matcher import ReferenceSet, is_match, l2_normalize
from .motion import MotionGate
from .quality import face_quality
from .references import reference_embeddings
from .tracking import FaceTracker
from .video_processor import SampledFrame, annotate, crop_with_margin, format_timestamp, sample_frames

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
            discover = p.get("mode") == "discover"
            reference_set, used_refs = None, []
            if not discover:
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

            ctx = _AnalysisContext.from_params(p, engine, reference_set, used_refs, job_dir(job))

            def frames_for(v: Video) -> int:
                step = max(1, int(round(v.fps / ctx.sample_fps))) if v.fps else 1
                return max(1, math.ceil(v.frame_count / step)) if v.frame_count else 1

            total = sum(frames_for(v) for v in videos)
            done = 0
            eta = EtaEstimator(total)

            for video in videos:
                job.current_video_id = video.id
                analyzer = _VideoAnalyzer(ctx, s, job, video)
                for frame in sample_frames(video.stored_path, ctx.sample_fps, fps=video.fps or None):
                    if cancel.is_set():
                        raise JobCancelled()
                    analyzer.process(frame)
                    done += 1
                    if done % PROGRESS_COMMIT_EVERY == 0:
                        job.progress = min(0.999, done / total)
                        job.eta_s = eta.update(done)
                        s.add(job)
                        s.commit()
                s.commit()
            frames = (f"{job.frames_processed} frames analisados ({job.frames_sampled} amostrados;"
                      f" {job.frames_skipped_motion} sem movimento, {job.frames_skipped_noface} sem faces na"
                      " 1.ª passagem)")
            if discover:
                _group_persons(ctx, s, job)
                summary = (f"{job.faces_detected} face(s) em {frames}, seguidas em {job.tracks} track(s) e"
                           f" agrupadas em {job.persons} pessoa(s) distinta(s) (limiar {ctx.threshold})")
            else:
                matched_tracks = s.exec(
                    select(Track).where(Track.job_id == job.id, Track.is_match == True)  # noqa: E712
                ).all()
                summary = f"{job.matches} correspondência(s) em {frames}"
                if ctx.tracking:
                    summary += f"; {len(matched_tracks)} track(s) com correspondência"
            job.progress = 1.0
            _finish(s, job, JobStatus.completed, (job.message + " " if job.message else "") + summary + ".")
        except JobCancelled:
            _finish_safely(s, job_id, JobStatus.cancelled, "Cancelada pelo utilizador.")
        except Exception as exc:
            log.error("Job %s falhou: %s", job_id, traceback.format_exc())
            _finish_safely(s, job_id, JobStatus.failed, f"Erro: {exc}")


def _finish_safely(s: Session, job_id: int, status: JobStatus, message: str) -> None:
    """Regista o fim da análise mesmo depois de um erro de base de dados.

    Um erro a meio (ex.: base de dados bloqueada, disco cheio) deixa a sessão à espera de rollback; sem isso,
    gravar o estado final também falharia e a análise ficaria para sempre "A decorrer".
    """
    try:
        s.rollback()
        job = s.get(Job, job_id)
        if job is not None:
            _finish(s, job, status, message)
        return
    except Exception:
        log.exception("Não foi possível registar o fim do job %s na sessão da análise", job_id)
    with Session(get_db_engine(), expire_on_commit=False) as s2:  # última tentativa, sessão nova
        job = s2.get(Job, job_id)
        if job is not None:
            _finish(s2, job, status, message)


def _finish(s: Session, job: Job, status: JobStatus, message: str) -> None:
    job.status = status
    job.message = message.strip()
    job.finished_at = utcnow()
    job.current_video_id = None
    job.eta_s = None
    s.add(job)
    s.commit()
    log_event(s, f"job.{status.value}", case_id=job.case_id, actor=job.created_by or "sistema",
              details={"job_id": job.id, "message": job.message, "matches": job.matches,
                       "frames_sampled": job.frames_sampled, "frames_processed": job.frames_processed,
                       "frames_skipped_motion": job.frames_skipped_motion,
                       "frames_skipped_noface": job.frames_skipped_noface, "tracks": job.tracks})


class EtaEstimator:
    """Tempo restante estimado pelo ritmo recente (janela móvel), que reflete a fase atual do vídeo: frames sem
    movimento ou sem faces são muito mais rápidos do que frames com várias faces."""

    WINDOW_S = 60.0
    MIN_ELAPSED_S = 5.0

    def __init__(self, total: int, clock=time.monotonic):
        self.total, self.clock = total, clock
        self.start = clock()
        self.history: deque[tuple[float, int]] = deque()

    def update(self, done: int) -> float | None:
        now = self.clock()
        self.history.append((now, done))
        while len(self.history) > 2 and now - self.history[0][0] > self.WINDOW_S:
            self.history.popleft()
        if now - self.start < self.MIN_ELAPSED_S or done <= 0:
            return None
        t0, d0 = self.history[0]
        if now - t0 >= self.MIN_ELAPSED_S and done > d0:
            rate = (done - d0) / (now - t0)
        else:
            rate = done / (now - self.start)
        return round(max(0, self.total - done) / rate, 1) if rate > 0 else None


TRACK_TOP_K = 3  # o match do track decide-se pela média das K melhores distâncias
DISCOVER_TOP_FACES = 5  # modo descoberta: o embedding do track é a média das K faces de melhor qualidade
DISCOVER_QUALITY_STEP = 0.05  # guarda uma nova face do track quando a qualidade melhora pelo menos isto
DISCOVER_SAVE_EVERY_S = 10.0  # ... ou quando passou este tempo desde a última face guardada do track


@dataclass
class _AnalysisContext:
    engine: FaceEngine
    reference_set: ReferenceSet | None  # None = modo descoberta
    refs: list[ReferenceImage]
    out_dir: Path
    model: str
    detector: str
    fast_detector: str | None
    sample_fps: float
    threshold: float
    candidate_threshold: float
    min_size: int
    min_conf: float
    min_quality: float
    upscale: float
    motion_filter: bool
    motion_threshold: float
    keyframe_interval_s: float
    tracking: bool
    track_gap_s: float
    discovered: list[_TrackState] = field(default_factory=list)  # modo descoberta: tracks de todos os vídeos
    det_embeddings: list[tuple[Detection, np.ndarray]] = field(default_factory=list)

    @property
    def discover(self) -> bool:
        return self.reference_set is None

    @classmethod
    def from_params(cls, p: dict, engine: FaceEngine, reference_set: ReferenceSet | None,
                    refs: list[ReferenceImage], out_dir: Path) -> _AnalysisContext:
        threshold = float(p["threshold"])
        fast = p.get("fast_detector") if p.get("cascade", False) else None
        return cls(
            engine=engine, reference_set=reference_set, refs=refs, out_dir=out_dir,
            model=p["model"], detector=p["detector"],
            fast_detector=fast if fast and fast != p["detector"] else None,
            sample_fps=float(p["sample_fps"]), threshold=threshold,
            candidate_threshold=threshold + float(p.get("candidate_margin", 0.0)),
            min_size=int(p.get("min_face_size", 0)), min_conf=float(p.get("min_confidence", 0.0)),
            min_quality=float(p.get("min_quality", 0.0)), upscale=float(p.get("upscale", 1.0)),
            motion_filter=bool(p.get("motion_filter", False)),
            motion_threshold=float(p.get("motion_threshold", 0.0)),
            keyframe_interval_s=float(p.get("keyframe_interval_s", 0.0)),
            tracking=bool(p.get("tracking", False)) or reference_set is None,
            track_gap_s=float(p.get("track_gap_s", 2.0)),
        )


@dataclass
class _TrackState:
    start_s: float
    end_s: float
    n_faces: int = 0
    top: list[float] = field(default_factory=list)  # as K menores distâncias, ordenadas
    best_distance: float = 2.0
    best_ref_id: int | None = None
    best_quality: float = 0.0
    best_saved_distance: float = 2.0
    row: Track | None = None
    faces: list[tuple[float, np.ndarray]] = field(default_factory=list)  # descoberta: (qualidade, embedding)
    best_saved_quality: float = -1.0
    last_saved_s: float = -1e9


class _VideoAnalyzer:
    """Processa os frames amostrados de um vídeo: movimento → cascata → deteção → qualidade → tracking → match."""

    def __init__(self, ctx: _AnalysisContext, s: Session, job: Job, video: Video):
        self.ctx, self.s, self.job, self.video = ctx, s, job, video
        self.gate = MotionGate(ctx.motion_threshold, ctx.keyframe_interval_s) if ctx.motion_filter else None
        self.tracker = FaceTracker(max_gap_s=ctx.track_gap_s) if ctx.tracking else None
        self.tracks: dict[int, _TrackState] = {}
        self.visible_tids: list[int] = []  # tracks vistos no último frame analisado

    def process(self, frame: SampledFrame) -> None:
        ctx, job = self.ctx, self.job
        job.frames_sampled += 1
        if self.gate is not None and not self.gate.check(frame.image, frame.timestamp_s).analyze:
            job.frames_skipped_motion += 1
            if self.tracker is not None:
                # A imagem é igual à do último frame analisado: as faces que lá estavam continuam presentes.
                self.tracker.keep_alive(frame.timestamp_s, self.visible_tids)
                for tid in self.visible_tids:
                    st = self.tracks[tid]
                    st.end_s = frame.timestamp_s
                    if st.row is not None:
                        self._refresh_track_row(st)
            return
        self.visible_tids = []
        if ctx.fast_detector and ctx.engine.count_faces(frame.image, detector=ctx.fast_detector,
                                                        upscale=ctx.upscale) == 0:
            job.frames_skipped_noface += 1
            return

        faces = ctx.engine.detect(frame.image, model=ctx.model, detector=ctx.detector, upscale=ctx.upscale,
                                  min_confidence=ctx.min_conf)
        job.frames_processed += 1
        kept = []
        for face in faces:
            if min(face.w, face.h) < ctx.min_size or face.confidence < ctx.min_conf:
                continue
            q = face_quality(frame.image, face.x, face.y, face.w, face.h, face.landmarks)
            if q.score < ctx.min_quality:
                continue
            kept.append((face, q))
        job.faces_detected += len(kept)
        if not kept:
            return

        track_ids: list[int | None] = [None] * len(kept)
        if self.tracker is not None:
            track_ids = list(self.tracker.update(
                frame.timestamp_s, [(f.x, f.y, f.w, f.h) for f, _ in kept], [f.embedding for f, _ in kept]))

        self.visible_tids = [t for t in track_ids if t is not None]
        if ctx.discover:
            self._process_discover(frame, kept, track_ids)
            return
        for k, ((face, q), tid) in enumerate(zip(kept, track_ids, strict=True)):
            result = ctx.reference_set.best_match(face.embedding)
            ref = ctx.refs[result.reference_index]
            state = self._track_state(tid, frame.timestamp_s, result.distance, ref.id, q.score)
            continuation = state is not None and state.row is not None
            if result.distance > ctx.candidate_threshold and not continuation:
                continue
            det = self._save_detection(frame, k, face, q, result.distance, ref.id)
            if state is not None:
                self._attach_to_track(state, det)

    def _process_discover(self, frame: SampledFrame, kept: list, track_ids: list[int | None]) -> None:
        """Sem referência: guarda as faces de cada track (a 1.ª, as que melhoram a qualidade e uma a cada
        DISCOVER_SAVE_EVERY_S) e acumula o embedding do track para o agrupamento final em pessoas."""
        for k, ((face, q), tid) in enumerate(zip(kept, track_ids, strict=True)):
            st = self.tracks.get(tid)
            if st is None:
                st = self.tracks[tid] = _TrackState(start_s=frame.timestamp_s, end_s=frame.timestamp_s)
                self.ctx.discovered.append(st)
            st.end_s = frame.timestamp_s
            st.n_faces += 1
            st.best_quality = max(st.best_quality, q.score)
            emb = l2_normalize(face.embedding)[0]
            if len(st.faces) < DISCOVER_TOP_FACES or q.score > st.faces[-1][0]:
                st.faces.append((q.score, emb))
                st.faces.sort(key=lambda f: -f[0])
                del st.faces[DISCOVER_TOP_FACES:]
            if (st.row is None or q.score >= st.best_saved_quality + DISCOVER_QUALITY_STEP
                    or frame.timestamp_s - st.last_saved_s >= DISCOVER_SAVE_EVERY_S):
                det = self._save_detection(frame, k, face, q, None, None)
                self.ctx.det_embeddings.append((det, emb))
                self._attach_discovered(st, det, q.score)
                st.last_saved_s = frame.timestamp_s
            elif st.row is not None:
                self._refresh_track_row(st)

    def _attach_discovered(self, st: _TrackState, det: Detection, quality: float) -> None:
        if st.row is None:
            st.row = Track(job_id=self.job.id, video_id=self.video.id, start_s=round(st.start_s, 3),
                           end_s=round(st.end_s, 3))
            self.s.add(st.row)
            self.job.tracks += 1
        self.s.flush()
        det.track_id = st.row.id
        st.row.n_saved += 1
        if quality > st.best_saved_quality:
            st.best_saved_quality = quality
            st.row.best_detection_id = det.id
        self._refresh_track_row(st)

    def _track_state(self, tid: int | None, ts: float, distance: float, ref_id: int,
                     quality: float) -> _TrackState | None:
        if tid is None:
            return None
        st = self.tracks.get(tid)
        if st is None:
            st = self.tracks[tid] = _TrackState(start_s=ts, end_s=ts)
        st.end_s = ts
        st.n_faces += 1
        if len(st.top) < TRACK_TOP_K or distance < st.top[-1]:
            bisect.insort(st.top, distance)
            del st.top[TRACK_TOP_K:]
        st.best_quality = max(st.best_quality, quality)
        if distance < st.best_distance:
            st.best_distance, st.best_ref_id = distance, ref_id
        if st.row is not None:
            self._refresh_track_row(st)
        return st

    def _save_detection(self, frame: SampledFrame, k: int, face, q, distance: float | None,
                        ref_id: int | None) -> Detection:
        """Guarda a deteção (recorte + frame anotado). distance=None: modo descoberta, sem referência."""
        ctx = self.ctx
        matched = distance is not None and is_match(distance, ctx.threshold)
        stem = f"v{self.video.id:04d}_f{frame.index:07d}_{k}"
        crop_path = ctx.out_dir / "crops" / f"{stem}.jpg"
        frame_path = ctx.out_dir / "frames" / f"{stem}.jpg"
        write_jpeg(crop_path, crop_with_margin(frame.image, face.x, face.y, face.w, face.h))
        if distance is None:
            label = f"{format_timestamp(frame.timestamp_s)} q={q.score:.2f}"
            color = (255, 160, 0)
            distance = 1.0  # substituída pela distância ao centro do grupo da pessoa no fim da análise
        else:
            label = f"{format_timestamp(frame.timestamp_s)} sim={1 - distance:.2f} q={q.score:.2f}"
            color = (0, 0, 255) if matched else (0, 165, 255)
        write_jpeg(frame_path, annotate(frame.image, face.x, face.y, face.w, face.h, label, color))
        det = Detection(
            job_id=self.job.id, video_id=self.video.id, reference_id=ref_id,
            frame_index=frame.index, timestamp_s=round(frame.timestamp_s, 3),
            x=face.x, y=face.y, w=face.w, h=face.h, face_confidence=round(face.confidence, 4),
            distance=round(distance, 4), similarity=round(1 - distance, 4), is_match=matched,
            crop_path=str(crop_path), frame_path=str(frame_path),
            quality=round(q.score, 3), sharpness=round(q.sharpness, 3), size_score=round(q.size, 3),
            frontal=None if q.frontal is None else round(q.frontal, 3),
        )
        self.s.add(det)
        if matched:
            self.job.matches += 1
        return det

    def _attach_to_track(self, st: _TrackState, det: Detection) -> None:
        if st.row is None:
            st.row = Track(job_id=self.job.id, video_id=self.video.id, start_s=round(st.start_s, 3),
                           end_s=round(st.end_s, 3))
            self.s.add(st.row)
            self.job.tracks += 1
        self.s.flush()  # um só flush: ids do track (se novo) e da deteção
        det.track_id = st.row.id
        st.row.n_saved += 1
        if det.distance < st.best_saved_distance:
            st.best_saved_distance = det.distance
            st.row.best_detection_id = det.id
        self._refresh_track_row(st)

    def _refresh_track_row(self, st: _TrackState) -> None:
        row = st.row
        top = st.top
        row.end_s = round(st.end_s, 3)
        row.n_faces = st.n_faces
        if self.ctx.discover:  # sem distâncias: são calculadas no agrupamento final
            row.best_quality = round(st.best_quality, 3)
            self.s.add(row)
            return
        row.best_distance = round(st.best_distance, 4)
        row.reference_id = st.best_ref_id
        row.track_distance = round(float(sum(top) / len(top)), 4)
        row.best_quality = round(st.best_quality, 3)
        row.is_match = row.track_distance <= self.ctx.threshold
        self.s.add(row)


def _track_embedding(st: _TrackState) -> np.ndarray:
    weights = np.array([max(qs, 0.05) for qs, _ in st.faces])
    return l2_normalize((np.stack([e for _, e in st.faces]) * weights[:, None]).sum(axis=0))[0]


def _group_persons(ctx: _AnalysisContext, s: Session, job: Job) -> None:
    """Modo descoberta: agrupa os tracks em pessoas e guarda a distância de cada track/face ao centro do grupo."""
    states = [st for st in ctx.discovered if st.row is not None and st.faces]
    if not states:
        job.persons = 0
        return
    embs = np.stack([_track_embedding(st) for st in states])
    weights = [len(st.faces) * (0.5 + st.best_quality) for st in states]
    labels = cluster_embeddings(embs, weights, ctx.threshold)
    # Numeração final: a pessoa 1 é a que tem mais faces detetadas (a que mais aparece).
    faces_per = np.bincount(labels, weights=[st.n_faces for st in states])
    order = np.argsort(-faces_per, kind="stable")
    remap = np.empty_like(order)
    remap[order] = np.arange(len(order))
    labels = remap[labels]
    centroids = centroids_of(embs, weights, labels)
    for st, label, emb in zip(states, labels, embs, strict=True):
        dist = float(1.0 - centroids[label] @ emb)
        st.row.person_id = int(label) + 1
        st.row.track_distance = st.row.best_distance = round(dist, 4)
        st.row.is_match = False
        s.add(st.row)
    person_of_track = {st.row.id: int(label) for st, label in zip(states, labels, strict=True)}
    for det, emb in ctx.det_embeddings:
        label = person_of_track.get(det.track_id)
        if label is None:
            continue
        dist = float(1.0 - centroids[label] @ emb)
        det.distance, det.similarity = round(dist, 4), round(1.0 - dist, 4)
        s.add(det)
    job.persons = int(labels.max()) + 1
    try:  # embeddings dos tracks, para reutilização futura (ex.: comparar pessoas entre análises)
        ctx.out_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(ctx.out_dir / "track_embeddings.npz", track_ids=[st.row.id for st in states],
                            person_ids=labels + 1, embeddings=embs.astype(np.float32))
    except OSError:
        log.warning("Não foi possível gravar os embeddings dos tracks do job %s", job.id)
