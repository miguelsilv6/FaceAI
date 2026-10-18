"""Execução das análises em segundo plano (uma de cada vez), com progresso e cancelamento."""

from __future__ import annotations

import bisect
import logging
import math
import os
import shutil
import threading
import time
import traceback
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sqlalchemy import update
from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine as get_db_engine
from ..models import Alert, Detection, Job, JobStatus, ReferenceImage, Track, Video, utcnow
from .audit import log_event
from .body_analysis import BodyCollector, ClothingParams, FaceLink
from .body_engine import get_body_engine
from .clustering import centroids_of, cluster_embeddings
from .face_engine import FaceEngine, get_engine
from .imageio import AsyncJpegWriter
from .matcher import MultiReferenceSet, is_match, l2_normalize
from .motion import MotionGate
from .quality import face_quality
from .references import person_groups, reference_embeddings
from .tracking import FaceTracker
from .video_processor import SampledFrame, annotate, crop_with_margin, format_timestamp, sample_frames

log = logging.getLogger(__name__)

COMMIT_EVERY_S = 1.5  # progresso e deteções gravados por tempo (não a cada N frames: frames estáticos são rápidos)
DECODE_PREFETCH = 4  # frames descodificados à frente, numa thread própria


class JobCancelled(Exception):
    pass


class _Timings:
    """Tempo acumulado por fase da análise (diagnóstico; fica em job.environment["timings"])."""

    def __init__(self) -> None:
        self.seconds: dict[str, float] = defaultdict(float)

    @contextmanager
    def __call__(self, phase: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[phase] += time.perf_counter() - t0

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 3) for k, v in sorted(self.seconds.items(), key=lambda kv: -kv[1])}


class JobInterrupted(Exception):
    """O servidor está a desligar: a análise volta à fila (não é um cancelamento pelo utilizador)."""


class _StopEvent(threading.Event):
    """Pedido de paragem da análise em curso; ``shutdown`` distingue o desligar do servidor do cancelamento."""

    shutdown = False


class JobRunner:
    """Fila de análises persistente (na base de dados), executada uma de cada vez por uma thread própria.

    A próxima análise é a de maior prioridade (empate: a mais antiga) que não esteja retida. As análises em fila
    sobrevivem a um reinício do servidor; as que estavam a decorrer são marcadas como interrompidas.
    """

    IDLE_WAIT_S = 5.0  # sem trabalho: volta a ver a fila (análises criadas noutro processo do servidor)

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stop = False
        self._current: tuple[int, threading.Event] | None = None

    enabled = True  # False neste processo se outro tiver o bloqueio do executor (acquire_runner_lock)

    def start(self) -> None:
        if not self.enabled:
            return
        with self._cond:
            if self._thread is not None and self._thread.is_alive():
                self._cond.notify_all()
                return
            self._stop = False
            self._thread = threading.Thread(target=self._loop, name="faceai-job", daemon=True)
            self._thread.start()

    def submit(self, job_id: int | None = None) -> None:
        """Acorda o worker (nova análise em fila, prioridade alterada ou análise libertada)."""
        self.start()

    wake = submit

    def cancel(self, job_id: int) -> bool:
        with self._cond:
            if self._current is not None and self._current[0] == job_id:
                self._current[1].set()
                return True
        with Session(get_db_engine(), expire_on_commit=False) as s:  # ainda em fila: cancela já
            job = s.get(Job, job_id)
            if job is not None and job.status == JobStatus.queued:
                _finish(s, job, JobStatus.cancelled, "Cancelada antes de iniciar.")
                return True
            if job is not None and job.status == JobStatus.running:
                # A correr noutro processo: o pedido fica na base de dados e é visto no próximo commit (≤ 1,5 s).
                job.cancel_requested = True
                s.add(job)
                s.commit()
                return True
        return False

    def shutdown(self, timeout: float = 30) -> None:
        with self._cond:
            self._stop = True
            if self._current is not None:
                stop = self._current[1]
                if isinstance(stop, _StopEvent):
                    stop.shutdown = True
                stop.set()
            self._cond.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None

    def _next_job_id(self) -> int | None:
        with Session(get_db_engine()) as s:
            return s.exec(select(Job.id).where(Job.status == JobStatus.queued, Job.held == False)  # noqa: E712
                          .order_by(Job.priority.desc(), Job.id).limit(1)).first()

    def _loop(self) -> None:
        _lower_thread_priority()
        while True:
            with self._cond:
                if self._stop:
                    return
            try:
                job_id = self._next_job_id()
            except Exception:  # pragma: no cover - base de dados indisponível (ex.: entre testes)
                log.exception("Fila de análises: erro ao escolher a próxima")
                job_id = None
            if job_id is None:
                with self._cond:
                    if not self._stop:
                        self._cond.wait(self.IDLE_WAIT_S)
                continue
            event = _StopEvent()
            with self._cond:
                self._current = (job_id, event)
            try:
                run_job(job_id, event)
            except Exception:  # pragma: no cover - registado no próprio job
                log.exception("Falha inesperada no job %s", job_id)
            finally:
                with self._cond:
                    self._current = None


def _lower_thread_priority() -> None:
    """FACEAI_JOB_NICE: no Linux o "nice" aplica-se só a esta thread (a interface continua fluida)."""
    if settings.job_nice <= 0 or not hasattr(os, "setpriority"):
        return
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), min(19, settings.job_nice))
    except OSError:  # pragma: no cover - sem permissão / plataforma sem suporte
        log.warning("Não foi possível baixar a prioridade da thread das análises")


def queue_positions(s: Session) -> dict[int, int]:
    """Posição (1 = a seguir) de cada análise em fila não retida."""
    rows = s.exec(select(Job.id).where(Job.status == JobStatus.queued, Job.held == False)  # noqa: E712
                  .order_by(Job.priority.desc(), Job.id)).all()
    return {jid: i + 1 for i, jid in enumerate(rows)}


runner = JobRunner()

_runner_lock_fh = None


def acquire_runner_lock() -> bool:
    """Um só executor de análises por pasta de dados. Com vários processos (ex.: `uvicorn --workers 4`), só o
    primeiro corre as análises e recupera as interrompidas; os outros servem a API. Sem isto, um segundo
    processo marcaria como "interrompidas" as análises que o primeiro está a correr."""
    global _runner_lock_fh
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows: um só processo
        return True
    path = settings.data_dir / "runner.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")  # noqa: SIM115 - fica aberto enquanto o processo viver
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        log.warning("Outro processo já executa as análises desta pasta de dados (%s): este só serve a API.", path)
        return False
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    _runner_lock_fh = fh
    return True


def release_runner_lock() -> None:
    global _runner_lock_fh
    if _runner_lock_fh is not None:
        _runner_lock_fh.close()  # fechar liberta o flock
        _runner_lock_fh = None


def job_dir(job: Job) -> Path:
    return settings.case_dir(job.case_id) / "jobs" / f"job_{job.id:05d}"


def recover_interrupted_jobs() -> None:
    """Análises que estavam a correr quando o servidor parou sem aviso (falha, corte de energia): ficam como
    falhadas e com resultados parciais (podem ser repetidas). Não voltam sozinhas à fila: se a própria análise
    fez cair o servidor (ex.: falta de memória), repeti-la automaticamente faria-o cair outra vez. As que estavam
    em fila continuam na fila e são executadas a seguir."""
    with Session(get_db_engine(), expire_on_commit=False) as s:
        for job in s.exec(select(Job).where(Job.status == JobStatus.running)):
            job.status = JobStatus.failed
            job.partial = True
            job.message = ("Interrompida: o servidor parou durante a análise. Os resultados estão incompletos; "
                           "use \"Repetir análise\".")
            job.finished_at = utcnow()
            job.current_video_id = None
            job.eta_s = None
            s.add(job)
            log_event(s, "job.interrupted", case_id=job.case_id, actor="sistema",
                      details={"job_id": job.id, "reason": "servidor parou sem aviso", "progress": job.progress,
                               "requeued": False}, commit=False)
        s.commit()


def reset_results(s: Session, job: Job) -> dict[str, int]:
    """Apaga os resultados de uma execução anterior da análise (deteções, tracks, alertas e ficheiros gerados),
    antes de a voltar a correr. Os frames guardados manualmente (pasta saved/) mantêm-se."""
    removed = {"detections": 0, "tracks": 0, "alerts": 0}
    for model, key in ((Alert, "alerts"), (Detection, "detections"), (Track, "tracks")):
        rows = list(s.exec(select(model).where(model.job_id == job.id)))
        removed[key] = len(rows)
        for row in rows:
            s.delete(row)
    s.flush()
    folder = job_dir(job)
    if folder.exists():
        for child in folder.iterdir():
            if child.name == "saved":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    for name in ("progress", "frames_processed", "faces_detected", "matches", "frames_sampled",
                 "frames_skipped_motion", "frames_skipped_noface", "tracks", "persons", "clothing_samples"):
        setattr(job, name, 0)
    job.clothing_summary = {}
    job.eta_s = None
    job.current_video_id = None
    return removed


def _has_results(s: Session, job: Job) -> bool:
    return (s.exec(select(Detection.id).where(Detection.job_id == job.id).limit(1)).first() is not None
            or s.exec(select(Track.id).where(Track.job_id == job.id).limit(1)).first() is not None
            or job.frames_sampled > 0)


def run_job(job_id: int, cancel: threading.Event) -> None:
    engine = get_engine()
    with Session(get_db_engine(), expire_on_commit=False) as s:
        job = s.get(Job, job_id)
        if job is None or job.status != JobStatus.queued:
            return
        if cancel.is_set():
            if not getattr(cancel, "shutdown", False):  # a desligar: fica em fila para o próximo arranque
                _finish(s, job, JobStatus.cancelled, "Cancelada antes de iniciar.")
            return
        # Reclamação atómica: só um processo passa de "em fila" para "a decorrer" (ex.: dois workers do uvicorn).
        claimed = s.execute(update(Job).where(Job.id == job_id, Job.status == JobStatus.queued)
                            .values(status=JobStatus.running).execution_options(synchronize_session=False))
        s.commit()
        if claimed.rowcount != 1:
            return
        s.refresh(job)
        p = job.params
        if job.partial or _has_results(s, job):
            # Retoma de uma análise interrompida ou repetida: começa do início, sem resultados antigos.
            removed = reset_results(s, job)
            log_event(s, "job.results_reset", case_id=job.case_id, actor="sistema",
                      details={"job_id": job.id, **removed}, commit=False)
        job.status = JobStatus.running
        job.partial = False
        job.cancel_requested = False
        job.message = ""
        job.started_at = utcnow()
        job.environment = {**engine.environment(),
                           "resources": {"cpu_threads": settings.cpu_threads or "auto", "job_nice": settings.job_nice}}
        s.add(job)
        s.commit()
        log_event(s, "job.started", case_id=job.case_id, actor=job.created_by,
                  details={"job_id": job.id, "params": p, "environment": job.environment})

        try:
            discover = p.get("mode") == "discover"
            reference_set, used_refs, persons = None, [], []
            if not discover:
                refs = [s.get(ReferenceImage, rid) for rid in job.reference_ids]
                refs = [r for r in refs if r is not None]
                used_refs, ref_matrix, warnings = reference_embeddings(
                    refs, engine, model=p["model"], detector=p["detector"]
                )
                if not used_refs:
                    raise ValueError("Nenhuma imagem de referência válida. " + " ".join(warnings))
                # Várias pessoas: as referências agrupam-se pelo rótulo (definido ao criar a análise).
                row_of = {r.id: i for i, r in enumerate(used_refs)}
                ordered: list[ReferenceImage] = []
                matrices = []
                for g in p.get("persons") or person_groups(refs):
                    rows = [row_of[rid] for rid in g["reference_ids"] if rid in row_of]
                    if not rows:
                        warnings.append(f"Pessoa '{g['label']}' ignorada: nenhuma referência válida.")
                        continue
                    persons.append({**g, "reference_ids": [used_refs[i].id for i in rows]})
                    ordered += [used_refs[i] for i in rows]
                    matrices.append(ref_matrix[rows])
                used_refs = ordered
                reference_set = MultiReferenceSet(matrices)
                job.reference_ids = [r.id for r in used_refs]
                job.params = {**p, "persons": persons}
                job.message = " ".join(warnings)

            videos = [v for v in (s.get(Video, vid) for vid in job.video_ids) if v is not None]
            if not videos:
                raise ValueError("Nenhum vídeo selecionado.")
            body = None
            if p.get("clothing") and not discover:
                body_engine = get_body_engine()
                body = BodyCollector(body_engine, ClothingParams.from_params(p), job_dir(job))
                job.environment = {**job.environment, "body": body_engine.environment()}

            ctx = _AnalysisContext.from_params(p, engine, reference_set, used_refs, job_dir(job))
            ctx.person_ids = [g["id"] for g in persons]

            def frames_for(v: Video) -> int:
                step = max(1, int(round(v.fps / ctx.sample_fps))) if v.fps else 1
                return max(1, math.ceil(v.frame_count / step)) if v.frame_count else 1

            total = sum(frames_for(v) for v in videos)
            done = 0
            eta = EtaEstimator(total)
            timings = ctx.timings
            started = time.perf_counter()

            def commit() -> None:
                with timings("save"):
                    ctx.writer.flush()  # nenhuma deteção é gravada sem os seus ficheiros
                with timings("db"):
                    s.commit()

            for video in videos:
                job.current_video_id = video.id
                if body is not None:
                    body.start_video(video, ctx.track_gap_s)
                analyzer = _VideoAnalyzer(ctx, s, job, video, body)
                frames_iter = sample_frames(video.source_path, ctx.sample_fps, fps=video.fps or None,
                                            prefetch=DECODE_PREFETCH)
                last_commit = time.monotonic()
                try:
                    while True:
                        with timings("decode"):
                            frame = next(frames_iter, None)
                        if frame is None:
                            break
                        if cancel.is_set():
                            raise JobInterrupted() if getattr(cancel, "shutdown", False) else JobCancelled()
                        analyzer.process(frame)
                        done += 1
                        if time.monotonic() - last_commit >= COMMIT_EVERY_S:
                            job.progress = min(0.999, done / total)
                            job.eta_s = eta.update(done)
                            s.add(job)
                            commit()
                            last_commit = time.monotonic()
                            if s.exec(select(Job.cancel_requested).where(Job.id == job.id)).first():
                                raise JobCancelled()  # pedido feito noutro processo (vários workers)
                finally:
                    frames_iter.close()  # pára a thread de descodificação (fim, cancelamento ou erro)
                commit()
            ctx.writer.close()
            job.environment = {**job.environment, "timings": {
                "total_s": round(time.perf_counter() - started, 3), "frames_per_s":
                    round(job.frames_sampled / max(1e-6, time.perf_counter() - started), 1), **timings.as_dict()}}
            frames = (f"{job.frames_processed} frames analisados ({job.frames_sampled} amostrados;"
                      f" {job.frames_skipped_motion} sem movimento, {job.frames_skipped_noface} sem faces na"
                      " 1.ª passagem)")
            if not discover:
                _save_track_embeddings(ctx, job, [st for st in ctx.tracked if st.row is not None and st.faces],
                                       lambda st: st.row.person_id or 1)
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
                if body is not None:
                    s.flush()
                    clothing = body.finalize(s, job)
                    job.clothing_samples, job.clothing_summary = clothing.get("samples", 0), clothing
                    summary += (f"; vestuário: {clothing.get('samples', 0)} amostra(s), "
                                f"{clothing.get('clothing_matches', 0)} aparição(ões) só pelo vestuário")
                    if clothing.get("note"):
                        summary += f" ({clothing['note']})"
            from .alerts import compute_alerts  # import local: alerts → linking → jobs

            alerts = compute_alerts(s, job, engine)
            if alerts:
                top = sum(1 for a in alerts if a.highlighted)
                summary += (f"; {len(alerts)} alerta(s) de pessoas do processo"
                            + (f" ({top} em vários locais)" if top else ""))
            job.progress = 1.0
            _finish(s, job, JobStatus.completed, (job.message + " " if job.message else "") + summary + ".")
        except JobCancelled:
            _close_writer(locals().get("ctx"))
            _finish_safely(s, job_id, JobStatus.cancelled, "Cancelada pelo utilizador.")
        except JobInterrupted:
            _close_writer(locals().get("ctx"))
            _requeue_after_shutdown(s, job_id)
        except Exception as exc:
            _close_writer(locals().get("ctx"))
            log.error("Job %s falhou: %s", job_id, traceback.format_exc())
            _finish_safely(s, job_id, JobStatus.failed, f"Erro: {exc}")


def _close_writer(ctx) -> None:
    """Depois de um erro ou cancelamento: termina as escritas pendentes sem esconder o erro original."""
    if ctx is None:
        return
    try:
        ctx.writer.close()
    except Exception:  # pragma: no cover - o erro principal já é registado pelo chamador
        log.exception("Escritas pendentes da análise falharam")


def _requeue_after_shutdown(s: Session, job_id: int) -> None:
    """Servidor a desligar (reinício, atualização): a análise volta à fila e recomeça do início no arranque."""
    try:
        s.rollback()
        job = s.get(Job, job_id)
        if job is None:
            return
        progress = job.progress
        job.status = JobStatus.queued
        job.partial = True
        job.message = "Interrompida ao desligar o servidor; recomeça do início no próximo arranque."
        job.current_video_id = None
        job.eta_s = None
        s.add(job)
        s.commit()
        log_event(s, "job.interrupted", case_id=job.case_id, actor="sistema",
                  details={"job_id": job_id, "reason": "servidor desligado", "progress": progress, "requeued": True})
    except Exception:  # pragma: no cover - fica "running" e o arranque seguinte marca-a como interrompida
        log.exception("Não foi possível voltar a pôr o job %s na fila", job_id)


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
    reference_set: MultiReferenceSet | None  # None = modo descoberta
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
    tracked: list[_TrackState] = field(default_factory=list)  # modo procurar: tracks de todos os vídeos
    person_ids: list[int] = field(default_factory=list)  # n.º de cada pessoa (pela ordem do reference_set)
    det_embeddings: list[tuple[int, int | None, np.ndarray]] = field(default_factory=list)  # (id, track, emb)
    writer: AsyncJpegWriter = field(default_factory=AsyncJpegWriter)  # recortes e frames gravados em paralelo
    timings: _Timings = field(default_factory=_Timings)

    @property
    def discover(self) -> bool:
        return self.reference_set is None

    @property
    def multi(self) -> bool:
        """Várias pessoas procuradas na mesma passagem (só então tracks e deteções levam person_id)."""
        return len(self.person_ids) > 1

    @classmethod
    def from_params(cls, p: dict, engine: FaceEngine, reference_set: MultiReferenceSet | None,
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
    tops: dict[int, list[float]] = field(default_factory=dict)  # por pessoa: as K menores distâncias, ordenadas
    best: dict[int, tuple[float, int]] = field(default_factory=dict)  # por pessoa: (menor distância, referência)
    best_quality: float = 0.0
    best_saved_distance: float = 2.0
    row: Track | None = None
    faces: list[tuple[float, np.ndarray]] = field(default_factory=list)  # (qualidade, embedding) das melhores faces
    best_saved_quality: float = -1.0
    last_saved_s: float = -1e9

    def person(self) -> int:
        """Pessoa do track: a de menor média das K melhores distâncias."""
        return min(self.tops, key=lambda p: sum(self.tops[p]) / len(self.tops[p]))

    def add_face(self, quality: float, emb: np.ndarray) -> None:
        if len(self.faces) < DISCOVER_TOP_FACES or quality > self.faces[-1][0]:
            self.faces.append((quality, emb))
            self.faces.sort(key=lambda f: -f[0])
            del self.faces[DISCOVER_TOP_FACES:]


class _VideoAnalyzer:
    """Processa os frames amostrados de um vídeo: movimento → cascata → deteção → qualidade → tracking → match."""

    def __init__(self, ctx: _AnalysisContext, s: Session, job: Job, video: Video,
                 body: BodyCollector | None = None):
        self.ctx, self.s, self.job, self.video = ctx, s, job, video
        self.body = body
        # A região da hora do DVR (lida por OCR ou indicada à mão) não conta como movimento.
        roi = (video.time_calibration or {}).get("roi")
        self.gate = (MotionGate(ctx.motion_threshold, ctx.keyframe_interval_s, mask_roi=roi)
                     if ctx.motion_filter else None)
        self.tracker = FaceTracker(max_gap_s=ctx.track_gap_s) if ctx.tracking else None
        self.tracks: dict[int, _TrackState] = {}
        self.visible_tids: list[int] = []  # tracks vistos no último frame analisado

    def process(self, frame: SampledFrame) -> None:
        self.job.frames_sampled += 1
        timings = self.ctx.timings
        if self.gate is not None:
            with timings("motion"):
                moving = self.gate.check(frame.image, frame.timestamp_s).analyze
            if not moving:
                self._skip_static(frame)
                return
        faces: list[FaceLink] = []
        self._process_faces(frame, faces)
        if self.body is not None:  # também nos frames sem faces (pessoa de costas): é aí que o vestuário ajuda
            with timings("clothing"):
                self.body.process(frame, faces)

    def _prefilter(self, frame: SampledFrame):
        """Filtro aplicado pelo motor antes do embedding (o passo caro): as faces abaixo do tamanho mínimo ou da
        qualidade mínima seriam descartadas a seguir de qualquer forma."""
        ctx = self.ctx
        if ctx.min_size <= 0 and ctx.min_quality <= 0:
            return None

        def keep(x: int, y: int, w: int, h: int, landmarks) -> bool:
            if min(w, h) < ctx.min_size:
                return False
            return ctx.min_quality <= 0 or face_quality(frame.image, x, y, w, h, landmarks).score >= ctx.min_quality

        return keep

    def _skip_static(self, frame: SampledFrame) -> None:
        self.job.frames_skipped_motion += 1
        if self.body is not None:
            self.body.keep_alive(frame.timestamp_s)
        if self.tracker is not None:
            # A imagem é igual à do último frame analisado: as faces que lá estavam continuam presentes.
            self.tracker.keep_alive(frame.timestamp_s, self.visible_tids)
            for tid in self.visible_tids:
                st = self.tracks[tid]
                st.end_s = frame.timestamp_s
                if st.row is not None:
                    self._refresh_track_row(st)

    def _process_faces(self, frame: SampledFrame, links: list[FaceLink]) -> None:
        ctx, job = self.ctx, self.job
        self.visible_tids = []
        if ctx.fast_detector:
            with ctx.timings("cascade"):
                n = ctx.engine.count_faces(frame.image, detector=ctx.fast_detector, upscale=ctx.upscale)
            if n == 0:
                job.frames_skipped_noface += 1
                return

        with ctx.timings("detect"):
            faces = ctx.engine.detect(frame.image, model=ctx.model, detector=ctx.detector, upscale=ctx.upscale,
                                      min_confidence=ctx.min_conf, prefilter=self._prefilter(frame))
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
            per = ctx.reference_set.per_person(face.embedding)  # [(referência, distância)] por pessoa
            pidx = min(range(len(per)), key=lambda i: per[i][1])
            ref, distance = ctx.refs[per[pidx][0]], per[pidx][1]
            state = self._track_state(tid, frame.timestamp_s, per, q.score, face.embedding)
            links.append(FaceLink((face.x, face.y, face.w, face.h), distance, state))
            continuation = state is not None and state.row is not None
            if distance > ctx.candidate_threshold and not continuation:
                continue
            det = self._save_detection(frame, k, face, q, distance, ref.id)
            if ctx.multi:
                det.person_id = ctx.person_ids[pidx]
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
            st.add_face(q.score, emb)
            if (st.row is None or q.score >= st.best_saved_quality + DISCOVER_QUALITY_STEP
                    or frame.timestamp_s - st.last_saved_s >= DISCOVER_SAVE_EVERY_S):
                det = self._save_detection(frame, k, face, q, None, None)
                self._attach_discovered(st, det, q.score)  # flush: a deteção já tem id e track
                # Só (id, track, embedding): manter os objetos ORM faria crescer a memória numa análise longa.
                self.ctx.det_embeddings.append((det.id, det.track_id, emb))
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

    def _track_state(self, tid: int | None, ts: float, per: list[tuple[int, float]], quality: float,
                     embedding: np.ndarray) -> _TrackState | None:
        if tid is None:
            return None
        st = self.tracks.get(tid)
        if st is None:
            st = self.tracks[tid] = _TrackState(start_s=ts, end_s=ts)
            self.ctx.tracked.append(st)
        st.end_s = ts
        st.n_faces += 1
        for p, (ref_idx, distance) in enumerate(per):
            top = st.tops.setdefault(p, [])
            if len(top) < TRACK_TOP_K or distance < top[-1]:
                bisect.insort(top, distance)
                del top[TRACK_TOP_K:]
            if p not in st.best or distance < st.best[p][0]:
                st.best[p] = (distance, self.ctx.refs[ref_idx].id)
        st.best_quality = max(st.best_quality, quality)
        st.add_face(quality, l2_normalize(embedding)[0])
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
        with ctx.timings("save"):
            ctx.writer.submit(crop_path, crop_with_margin(frame.image, face.x, face.y, face.w, face.h))
        if distance is None:
            label = f"{format_timestamp(frame.timestamp_s)} q={q.score:.2f}"
            color = (255, 160, 0)
            distance = 1.0  # substituída pela distância ao centro do grupo da pessoa no fim da análise
        else:
            label = f"{format_timestamp(frame.timestamp_s)} sim={1 - distance:.2f} q={q.score:.2f}"
            color = (0, 0, 255) if matched else (0, 165, 255)
        with ctx.timings("save"):
            ctx.writer.submit(frame_path, annotate(frame.image, face.x, face.y, face.w, face.h, label, color))
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
        row.end_s = round(st.end_s, 3)
        row.n_faces = st.n_faces
        if self.ctx.discover:  # sem distâncias: são calculadas no agrupamento final
            row.best_quality = round(st.best_quality, 3)
            self.s.add(row)
            return
        p = st.person()
        top = st.tops[p]
        row.best_distance = round(st.best[p][0], 4)
        row.reference_id = st.best[p][1]
        row.track_distance = round(float(sum(top) / len(top)), 4)
        row.person_id = self.ctx.person_ids[p] if self.ctx.multi else None
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
    rows = []
    for det_id, track_id, emb in ctx.det_embeddings:
        label = person_of_track.get(track_id)
        if label is None:
            continue
        dist = float(1.0 - centroids[label] @ emb)
        rows.append({"id": det_id, "distance": round(dist, 4), "similarity": round(1.0 - dist, 4)})
    if rows:
        s.flush()
        s.execute(update(Detection), rows)  # atualização em massa pela chave primária
        for obj in list(s.identity_map.values()):  # objetos ainda em memória: voltam a ler os valores novos
            if isinstance(obj, Detection):
                s.expire(obj, ["distance", "similarity"])
    job.persons = int(labels.max()) + 1
    _save_track_embeddings(ctx, job, states, lambda st: st.row.person_id, embs)


def _save_track_embeddings(ctx: _AnalysisContext, job: Job, states: list[_TrackState], person_of,
                           embs: np.ndarray | None = None) -> None:
    """Embeddings dos tracks (média ponderada das melhores faces), para comparar pessoas entre análises."""
    if not states:
        return
    if embs is None:
        embs = np.stack([_track_embedding(st) for st in states])
    try:
        ctx.out_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(ctx.out_dir / "track_embeddings.npz", track_ids=[st.row.id for st in states],
                            person_ids=[int(person_of(st)) for st in states], embeddings=embs.astype(np.float32),
                            model=np.array(ctx.model))
    except OSError:
        log.warning("Não foi possível gravar os embeddings dos tracks do job %s", job.id)
