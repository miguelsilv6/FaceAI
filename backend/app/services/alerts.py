"""Alertas de pessoas do processo numa análise.

- Modo descoberta: cada pessoa descoberta é comparada com as fotos das pessoas do processo (embeddings do mesmo
  modelo e detetor da análise). Abaixo do limiar de correspondência do modelo → alerta "a Pessoa #3 pode ser a
  Pessoa A". Destacado se aparecer em ≥ N locais.
- Modo procurar: alerta destacado quando uma pessoa procurada tem correspondências em ≥ N locais.
- Modo descoberta, sem fotos: alerta "places" quando uma pessoa descoberta aparece em ≥ N locais, e "linked" quando
  se parece com uma pessoa ou aparição de outra análise do mesmo processo (mesmo modelo).

São indícios para revisão humana (confirmar/descartar), não identificações.
"""

from __future__ import annotations

import logging

import numpy as np
from sqlmodel import Session, select

from ..models import Alert, CasePerson, Detection, Job, ReferenceImage, Track, Video
from .audit import log_event
from .clock import real_time

log = logging.getLogger(__name__)


def _places(s: Session, tracks: list[Track]) -> tuple[list[int], list[int], str | None, str | None]:
    """Locais e vídeos (pela ordem da 1.ª aparição) e hora real da primeira e última aparição."""
    videos: dict[int, Video | None] = {}
    starts, ends = [], []
    for t in sorted(tracks, key=lambda t: (t.video_id, t.start_s)):
        if t.video_id not in videos:
            videos[t.video_id] = s.get(Video, t.video_id)
        v = videos[t.video_id]
        if v is None:
            continue
        a = real_time(v.dvr_start, v.dvr_rate, v.clock_offset_s, t.start_s)
        b = real_time(v.dvr_start, v.dvr_rate, v.clock_offset_s, t.end_s)
        if a:
            starts.append(a.isoformat(timespec="seconds"))
        if b:
            ends.append(b.isoformat(timespec="seconds"))
    locs = list(dict.fromkeys(v.location_id for v in videos.values() if v is not None and v.location_id))
    return locs, [vid for vid, v in videos.items() if v is not None], min(starts, default=None), \
        max(ends, default=None)


def _watched(s: Session, job: Job) -> list[tuple[CasePerson, list[ReferenceImage]]]:
    p = job.params or {}
    wanted = p.get("watch_person_ids")
    out = []
    for cp in s.exec(select(CasePerson).where(CasePerson.case_id == job.case_id).order_by(CasePerson.id)):
        if wanted is not None and cp.id not in wanted:
            continue
        refs = list(s.exec(select(ReferenceImage).where(ReferenceImage.case_person_id == cp.id,
                                                        ReferenceImage.valid == True)))  # noqa: E712
        if refs:
            out.append((cp, refs))
    return out


def _discover_alerts(s: Session, job: Job, engine) -> list[Alert]:
    from .face_engine import default_threshold
    from .linking import _ref_embedding, load_index
    from .matcher import l2_normalize

    idx = load_index(job)
    if idx is None or not len(idx.track_ids):
        return []
    p = job.params or {}
    model, detector = p.get("model", ""), p.get("detector", "")
    threshold = default_threshold(model, engine)  # limiar de correspondência, não o de agrupamento
    tracks = {t.id: t for t in s.exec(select(Track).where(Track.job_id == job.id, Track.kind == "face"))}
    alerts = []
    for cp, refs in _watched(s, job):
        vecs = [e for r in refs if (e := _ref_embedding(r, engine, model, detector)) is not None]
        if not vecs:
            continue
        query = l2_normalize(np.stack(vecs))
        for pid in sorted(set(int(x) for x in idx.person_ids if x > 0)):
            rows = np.flatnonzero(idx.person_ids == pid)
            embs = idx.embeddings[rows]
            centroid = embs.mean(axis=0)
            centroid = centroid / (np.linalg.norm(centroid) or 1.0)
            # A melhor de: centróide da pessoa e média dos 3 tracks mais próximos (robusto a um track mau).
            d_centroid = float((1.0 - query @ centroid).min())
            per_track = np.sort((1.0 - query @ embs.T).min(axis=0))
            d_tracks = float(per_track[: min(3, len(per_track))].mean())
            dist = min(d_centroid, d_tracks)
            if dist > threshold:
                continue
            person_tracks = [tracks[int(t)] for t in idx.track_ids[rows] if int(t) in tracks]
            locs, vids, first, last = _places(s, person_tracks)
            best = max(person_tracks, key=lambda t: (t.best_quality, t.n_faces), default=None)
            alerts.append(Alert(case_id=job.case_id, job_id=job.id, case_person_id=cp.id, discovered_person_id=pid,
                                distance=round(dist, 4), similarity=round(1.0 - dist, 4), location_ids=locs,
                                video_ids=vids, n_tracks=len(person_tracks), first_real=first, last_real=last,
                                best_detection_id=best.best_detection_id if best else None))
    return alerts


def _match_alerts(s: Session, job: Job, min_locations: int) -> list[Alert]:
    """Modo procurar: correspondências efetivas (deteção abaixo do limiar ou num track com correspondência)."""
    p = job.params or {}
    single = len(p.get("persons") or []) == 1  # uma só pessoa: tracks e deteções não levam person_id
    groups = [g for g in p.get("persons") or [] if g.get("case_person_id")]
    wanted = p.get("watch_person_ids")
    match_tracks = {t.id: t for t in s.exec(select(Track).where(Track.job_id == job.id,
                                                                Track.is_match == True))}  # noqa: E712
    dets = [d for d in s.exec(select(Detection).where(Detection.job_id == job.id))
            if (d.kind or "face") == "face" and (d.is_match or d.track_id in match_tracks)]
    alerts = []
    for g in groups:
        if wanted is not None and g["case_person_id"] not in wanted:
            continue
        mine = [d for d in dets if single or d.person_id == g["id"]
                or (d.track_id in match_tracks and match_tracks[d.track_id].person_id == g["id"])]
        if not mine:
            continue
        spans = [Track(job_id=job.id, video_id=d.video_id, start_s=d.timestamp_s, end_s=d.timestamp_s) for d in mine]
        locs, vids, first, last = _places(s, spans)
        if len(locs) < min_locations:
            continue
        best = min(mine, key=lambda d: d.distance)
        n_tracks = len({d.track_id for d in mine if d.track_id is not None}) or len(mine)
        alerts.append(Alert(case_id=job.case_id, job_id=job.id, case_person_id=g["case_person_id"],
                            distance=round(best.distance, 4), similarity=round(1.0 - best.distance, 4),
                            location_ids=locs, video_ids=vids, n_tracks=n_tracks, first_real=first, last_real=last,
                            best_detection_id=best.id))
    return alerts


MAX_LINKED = 50  # ligações a outras análises por análise (as mais próximas)


def _person_tracks(s: Session, job: Job) -> dict[int, list[Track]]:
    out: dict[int, list[Track]] = {}
    for t in s.exec(select(Track).where(Track.job_id == job.id, Track.kind == "face")):
        if t.person_id:
            out.setdefault(t.person_id, []).append(t)
    return out


def _places_alerts(s: Session, job: Job, min_locations: int, covered: set[int]) -> list[Alert]:
    """Pessoas descobertas (desconhecidas) vistas em ≥ N locais; as já associadas a uma pessoa do processo não."""
    alerts = []
    for pid, ts in sorted(_person_tracks(s, job).items()):
        if pid in covered:
            continue
        locs, vids, first, last = _places(s, ts)
        if len(locs) < min_locations:
            continue
        best = max(ts, key=lambda t: (t.best_quality, t.n_faces))
        alerts.append(Alert(case_id=job.case_id, job_id=job.id, kind="places", discovered_person_id=pid,
                            location_ids=locs, video_ids=vids, n_tracks=len(ts), first_real=first, last_real=last,
                            best_detection_id=best.best_detection_id))
    return alerts


def _linked_alerts(s: Session, job: Job, engine) -> list[Alert]:
    """Pessoas descobertas parecidas com pessoas/aparições de outras análises concluídas do mesmo processo."""
    from .face_engine import default_threshold
    from .linking import _indexed_jobs, _search, load_index

    idx = load_index(job)
    if idx is None or not len(idx.track_ids):
        return []
    others, _ = _indexed_jobs(s, case_ids={job.case_id}, exclude_job_id=job.id)
    others = [(j, i) for j, i in others if i.model == idx.model]
    if not others:
        return []
    threshold = default_threshold((job.params or {}).get("model", ""), engine)
    by_person = _person_tracks(s, job)
    found: list[tuple[float, int, Job, int]] = []  # (distância, pessoa desta análise, outra análise, track)
    for pid in sorted(by_person):
        rows = np.flatnonzero(idx.person_ids == pid)
        if not len(rows):
            continue
        # Como nos alertas de pessoas do processo: centróide e cada um dos tracks; conta o mais próximo.
        embs = idx.embeddings[rows]
        centroid = embs.mean(axis=0)
        query = np.vstack([centroid / (np.linalg.norm(centroid) or 1.0), embs])
        hits, *_ = _search(others, lambda _j, _i, q=query: q, threshold=threshold)
        best: dict[int, tuple[float, Job, int]] = {}
        for dist, other, tid in hits:  # já ordenados pela distância: o primeiro de cada análise é o melhor
            best.setdefault(other.id, (dist, other, tid))
        found += [(d, pid, o, t) for d, o, t in best.values()]
    alerts = []
    for dist, pid, other, tid in sorted(found, key=lambda x: x[0])[:MAX_LINKED]:
        t_other = s.get(Track, tid)
        if t_other is None:
            continue
        locs_here, vids_here, first, last = _places(s, by_person[pid])
        locs_other, vids_other, _, _ = _places(s, [t_other])
        best_here = max(by_person[pid], key=lambda t: (t.best_quality, t.n_faces))
        alerts.append(Alert(case_id=job.case_id, job_id=job.id, kind="linked", discovered_person_id=pid,
                            linked_job_id=other.id, linked_track_id=tid, linked_person_id=t_other.person_id,
                            distance=round(dist, 4), similarity=round(1.0 - dist, 4),
                            location_ids=list(dict.fromkeys(locs_here + locs_other)),
                            video_ids=list(dict.fromkeys(vids_here + vids_other)),
                            n_tracks=len(by_person[pid]), first_real=first, last_real=last,
                            best_detection_id=best_here.best_detection_id))
    return alerts


def compute_alerts(s: Session, job: Job, engine) -> list[Alert]:
    """Cria (e grava) os alertas de uma análise acabada de concluir. Nunca faz a análise falhar."""
    p = job.params or {}
    min_locations = int(p.get("alert_min_locations") or 2)
    alerts: list[Alert] = []
    # Cada tipo à parte: uma falha num deles não apaga os outros (os alertas são complementares).
    try:
        if p.get("watch_persons", True):
            alerts = (_discover_alerts(s, job, engine) if p.get("mode") == "discover"
                      else _match_alerts(s, job, min_locations))
        for a in alerts:
            a.highlighted = len(a.location_ids) >= min_locations
    except Exception:  # noqa: BLE001
        log.exception("Falha a calcular os alertas de pessoas do processo da análise %s", job.id)
        alerts = []
    if p.get("mode") == "discover":
        covered = {a.discovered_person_id for a in alerts if a.discovered_person_id is not None}
        try:
            for a in _places_alerts(s, job, min_locations, covered):
                a.highlighted = True
                alerts.append(a)
        except Exception:  # noqa: BLE001
            log.exception("Falha a calcular os alertas de locais da análise %s", job.id)
        try:
            alerts += _linked_alerts(s, job, engine)
        except Exception:  # noqa: BLE001
            log.exception("Falha a ligar a análise %s às outras análises do processo", job.id)
    for a in alerts:
        s.add(a)
    s.flush()
    for a in alerts:
        log_event(s, "alert.created", case_id=job.case_id, actor="sistema",
                  details={"alert_id": a.id, "kind": a.kind, "job_id": job.id, "case_person_id": a.case_person_id,
                           "linked_job_id": a.linked_job_id, "linked_track_id": a.linked_track_id,
                           "discovered_person_id": a.discovered_person_id, "similarity": a.similarity,
                           "location_ids": a.location_ids, "highlighted": a.highlighted})
    return alerts
