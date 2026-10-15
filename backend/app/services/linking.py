"""Ligar pessoas entre análises: compara o embedding de um track (ou de uma pessoa) com os tracks de outras
análises dos casos a que o utilizador tem acesso.

Cada análise grava, no fim, os embeddings dos seus tracks (track_embeddings.npz: média ponderada das faces de
melhor qualidade). Só se comparam análises feitas com o mesmo modelo de reconhecimento (embeddings de modelos
diferentes não são comparáveis).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlmodel import Session, select

from ..models import Case, Job, JobStatus, ReferenceImage, Track, Video
from .clock import format_real, real_time
from .face_engine import FaceEngine
from .jobs import job_dir
from .matcher import l2_normalize
from .references import reference_embeddings

INDEX_FILE = "track_embeddings.npz"
_CACHE_MAX = 64
_cache: OrderedDict[tuple[str, float], TrackIndex] = OrderedDict()
_lock = threading.Lock()


@dataclass(frozen=True)
class TrackIndex:
    track_ids: np.ndarray
    person_ids: np.ndarray
    embeddings: np.ndarray  # normalizados
    model: str


def load_index(job: Job) -> TrackIndex | None:
    path = job_dir(job) / INDEX_FILE
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return None
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    try:
        with np.load(path) as z:
            model = str(z["model"]) if "model" in z.files else str((job.params or {}).get("model", ""))
            idx = TrackIndex(z["track_ids"].astype(int), z["person_ids"].astype(int),
                             l2_normalize(z["embeddings"]), model)
    except (OSError, KeyError, ValueError):
        return None
    with _lock:
        _cache[key] = idx
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return idx


def track_query(job: Job, track_id: int) -> tuple[np.ndarray, str] | None:
    idx = load_index(job)
    if idx is None:
        return None
    hit = np.flatnonzero(idx.track_ids == track_id)
    return (idx.embeddings[hit[0]], idx.model) if len(hit) else None


def person_query(job: Job, person_id: int) -> tuple[np.ndarray, str] | None:
    idx = load_index(job)
    if idx is None:
        return None
    rows = np.flatnonzero(idx.person_ids == person_id)
    if not len(rows):
        return None
    return l2_normalize(idx.embeddings[rows].mean(axis=0))[0], idx.model


def find_similar(s: Session, query: np.ndarray, model: str, *, case_ids: set[int] | None,
                 exclude_job_id: int | None, threshold: float, limit: int = 100) -> dict:
    """Tracks de outras análises (concluídas, mesmo modelo) a uma distância ≤ threshold, do mais próximo para o
    mais afastado. case_ids=None: todos os casos (administrador). query: um vetor ou uma matriz (N×D) — com várias
    consultas conta a mais próxima (o mesmo critério da análise com várias fotos)."""
    hits, searched, not_indexed, other_model = _search(s, {model: query}, case_ids=case_ids,
                                                       exclude_job_id=exclude_job_id, threshold=threshold)
    return {"model": model, "threshold": threshold, "results": _results(s, hits, limit), "jobs_searched": searched,
            "jobs_not_indexed": not_indexed, "jobs_other_model": other_model}


def search_by_references(s: Session, refs: list[ReferenceImage], engine: FaceEngine, *, case_ids: set[int] | None,
                         threshold: float | None, limit: int = 100) -> dict:
    """Procura as pessoas destas fotos de referência nos tracks das análises concluídas (sem reprocessar vídeo).

    Os embeddings das fotos são calculados com o modelo/detetor de cada análise (embeddings de modelos diferentes não
    são comparáveis). threshold=None: usa o limiar de cada análise."""
    jobs = [j for j in _candidate_jobs(s, case_ids=case_ids, exclude_job_id=None) if load_index(j) is not None]
    combos = sorted({((j.params or {}).get("model", ""), (j.params or {}).get("detector", "")) for j in jobs})
    queries: dict[str, np.ndarray] = {}
    detector_of: dict[str, str] = {}
    warnings: list[str] = []
    for model, detector in combos:
        if not model or model in queries:
            continue
        try:
            _, matrix, warns = reference_embeddings(refs, engine, model=model, detector=detector)
        except Exception as exc:  # noqa: BLE001 - modelo indisponível neste servidor
            warnings.append(f"Modelo '{model}' indisponível: {exc}")
            continue
        warnings.extend(w for w in warns if w not in warnings)
        if len(matrix):
            queries[model], detector_of[model] = matrix, detector
    hits, searched, not_indexed, other_model = _search(s, queries, case_ids=case_ids, exclude_job_id=None,
                                                       threshold=threshold)
    return {"model": ", ".join(sorted(queries)) or "—", "threshold": threshold, "results": _results(s, hits, limit),
            "jobs_searched": searched, "jobs_not_indexed": not_indexed, "jobs_other_model": other_model,
            "warnings": warnings, "references": [r.id for r in refs]}


def _candidate_jobs(s: Session, *, case_ids: set[int] | None, exclude_job_id: int | None) -> list[Job]:
    q = select(Job).where(Job.status == JobStatus.completed)
    if exclude_job_id is not None:
        q = q.where(Job.id != exclude_job_id)
    if case_ids is not None:
        q = q.where(Job.case_id.in_(case_ids or [-1]))
    return list(s.exec(q.order_by(Job.id)))


def _search(s: Session, queries: dict[str, np.ndarray], *, case_ids: set[int] | None, exclude_job_id: int | None,
            threshold: float | None) -> tuple[list[tuple[float, Job, int]], int, int, int]:
    """queries: {modelo: vetor ou matriz}. threshold=None: o limiar de cada análise."""
    hits: list[tuple[float, Job, int]] = []
    searched = not_indexed = other_model = 0
    for job in _candidate_jobs(s, case_ids=case_ids, exclude_job_id=exclude_job_id):
        idx = load_index(job)
        if idx is None:
            not_indexed += 1
            continue
        query = queries.get(idx.model)
        if query is None:
            other_model += 1
            continue
        searched += 1
        th = threshold if threshold is not None else float((job.params or {}).get("threshold") or 0.5)
        q = l2_normalize(np.atleast_2d(query))
        d = (1.0 - idx.embeddings @ q.T).min(axis=1)
        for i in np.flatnonzero(d <= th):
            hits.append((float(d[i]), job, int(idx.track_ids[i])))
    hits.sort(key=lambda h: h[0])
    return hits, searched, not_indexed, other_model


def _results(s: Session, hits: list[tuple[float, Job, int]], limit: int) -> list[dict]:
    results = []
    cases: dict[int, Case | None] = {}
    videos: dict[int, Video | None] = {}
    for dist, job, tid in hits[:limit]:
        t = s.get(Track, tid)
        if t is None or t.job_id != job.id:
            continue
        if job.case_id not in cases:
            cases[job.case_id] = s.get(Case, job.case_id)
        if t.video_id not in videos:
            videos[t.video_id] = s.get(Video, t.video_id)
        v, c = videos[t.video_id], cases[job.case_id]
        rt = real_time(v.dvr_start, v.dvr_rate, v.clock_offset_s, t.start_s) if v else None
        results.append({
            "case_id": job.case_id, "case_name": c.name if c else "", "job_id": job.id,
            "job_mode": (job.params or {}).get("mode", "match"), "track_id": t.id, "person_id": t.person_id,
            "video_id": t.video_id, "video_filename": v.filename if v else "", "start_s": t.start_s,
            "end_s": t.end_s, "real_start": format_real(rt) or None, "distance": round(dist, 4),
            "similarity": round(1.0 - dist, 4), "best_detection_id": t.best_detection_id,
            "review_status": t.review_status, "is_match": t.is_match,
        })
    return results


def index_exists(job: Job) -> bool:
    return Path(job_dir(job) / INDEX_FILE).exists()
