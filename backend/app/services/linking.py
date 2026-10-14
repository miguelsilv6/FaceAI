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

from ..models import Case, Job, JobStatus, Track, Video
from .clock import format_real, real_time
from .jobs import job_dir
from .matcher import l2_normalize

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


def find_similar(s: Session, query: np.ndarray, model: str, *, case_ids: set[int] | None, exclude_job_id: int,
                 threshold: float, limit: int = 100) -> dict:
    """Tracks de outras análises (concluídas, mesmo modelo) a uma distância ≤ threshold, do mais próximo para o
    mais afastado. case_ids=None: todos os casos (administrador)."""
    q = select(Job).where(Job.status == JobStatus.completed, Job.id != exclude_job_id)
    if case_ids is not None:
        q = q.where(Job.case_id.in_(case_ids or [-1]))
    hits: list[tuple[float, Job, int]] = []
    searched = not_indexed = other_model = 0
    for job in s.exec(q):
        idx = load_index(job)
        if idx is None:
            not_indexed += 1
            continue
        if idx.model != model:
            other_model += 1
            continue
        searched += 1
        d = 1.0 - idx.embeddings @ query
        for i in np.flatnonzero(d <= threshold):
            hits.append((float(d[i]), job, int(idx.track_ids[i])))
    hits.sort(key=lambda h: h[0])
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
    return {"model": model, "threshold": threshold, "results": results, "jobs_searched": searched,
            "jobs_not_indexed": not_indexed, "jobs_other_model": other_model}


def index_exists(job: Job) -> bool:
    return Path(job_dir(job) / INDEX_FILE).exists()
