"""Análises, resultados, revisão humana e exportações."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlmodel import select

from ..config import settings
from ..models import (
    Alert,
    AuditEvent,
    CasePerson,
    Detection,
    Job,
    JobStatus,
    ReferenceImage,
    ReviewStatus,
    SavedFrame,
    Track,
    Video,
    utcnow,
)
from ..schemas import (
    AlertUpdate,
    JobCreate,
    JobDelete,
    PersonMerge,
    QueueUpdate,
    ReviewUpdate,
    SavedFrameCreate,
    TrackPerson,
)
from ..services import disk, enhance, linking, reports, validated_frames
from ..services.audit import log_event
from ..services.body_engine import DEFAULT_PERSON_DETECTOR, DEFAULT_REID_THRESHOLD, PERSON_DETECTORS
from ..services.clock import real_time
from ..services.face_engine import default_threshold, get_engine
from ..services.frames import extract_frame
from ..services.imageio import read_image, write_jpeg
from ..services.jobs import job_dir, runner
from ..services.locations import locations_by_id, video_place
from ..services.persons import person_segments, summarize_persons
from ..services.references import person_groups
from ..services.video_processor import format_timestamp
from .cases import register_reference
from .deps import (
    ActorDep,
    CurrentUser,
    SessionDep,
    accessible_case_ids,
    authorize_objects,
    can_manage_case,
    get_case_or_404,
    get_job_or_404,
)

# Todas as rotas exigem sessão; os objetos do caminho têm de pertencer a um caso acessível (ver deps).
router = APIRouter(prefix="/api", tags=["análises"], dependencies=[Depends(authorize_objects)])


@router.post("/cases/{case_id}/jobs", response_model=Job, status_code=201)
def create_job(case_id: int, body: JobCreate, session: SessionDep, actor: ActorDep) -> Job:
    get_case_or_404(session, case_id)
    if disk.disk_status()["level"] == "critical":
        disk.check_space(0)
        raise HTTPException(507, "Disco do servidor quase cheio: liberte espaço antes de iniciar novas análises.")
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
    discover = params.mode == "discover"
    if discover:
        params.tracking = True  # as pessoas formam-se a partir dos tracks
        params.clothing = False  # o vestuário parte das correspondências faciais (modo "procurar uma pessoa")
    if params.clothing:
        params.person_detector = params.person_detector or DEFAULT_PERSON_DETECTOR
        if params.person_detector not in PERSON_DETECTORS:
            raise HTTPException(422, f"Detetor de pessoas desconhecido: {params.person_detector}")
        params.reid_threshold = params.reid_threshold or DEFAULT_REID_THRESHOLD

    videos = list(session.exec(select(Video).where(Video.case_id == case_id).order_by(Video.id)))
    locs = locations_by_id(session, case_id)
    if body.location_ids is not None and any(i not in locs for i in body.location_ids):
        raise HTTPException(422, "Local inexistente neste processo.")
    if body.video_ids is None and body.location_ids is None:
        video_ids = [v.id for v in videos]
    else:  # união: os vídeos indicados e todas as câmaras dos locais indicados
        wanted, wanted_locs = set(body.video_ids or []), set(body.location_ids or [])
        video_ids = [v.id for v in videos if v.id in wanted or v.location_id in wanted_locs]
    if not video_ids:
        raise HTTPException(422, "Selecione pelo menos um vídeo do processo.")
    ref_ids: list[int] = []
    job_params = params.model_dump()
    # Locais abrangidos (nome no momento da análise, para o relatório e a reprodutibilidade)
    chosen_videos = [v for v in videos if v.id in video_ids]
    job_params["locations"] = [
        {"id": lid, "name": locs[lid].name if lid in locs else "",
         "video_ids": [v.id for v in chosen_videos if v.location_id == lid]}
        for lid in dict.fromkeys(v.location_id for v in chosen_videos)]
    if not discover:
        refs = list(session.exec(select(ReferenceImage).where(ReferenceImage.case_id == case_id)
                                 .order_by(ReferenceImage.id)))
        chosen = [r for r in refs if r.valid and (body.reference_ids is None or r.id in body.reference_ids)]
        ref_ids = [r.id for r in chosen]
        # Várias pessoas na mesma passagem: as referências agrupam-se pelo rótulo de pessoa.
        names = {p.id: p.name for p in session.exec(select(CasePerson).where(CasePerson.case_id == case_id))}
        job_params["persons"] = person_groups(chosen, names)
        if not ref_ids:
            raise HTTPException(422, "Não há imagens de referência válidas selecionadas. Sem amostra da pessoa, "
                                     "use o modo \"Descobrir todas as faces\".")

    job = Job(case_id=case_id, params=job_params, video_ids=video_ids, reference_ids=ref_ids,
              created_by=actor)
    session.add(job)
    session.commit()
    session.refresh(job)
    log_event(session, "job.created", case_id=case_id, actor=actor,
              details={"job_id": job.id, "params": job.params, "video_ids": video_ids, "reference_ids": ref_ids,
                       "location_ids": body.location_ids})
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


@router.patch("/jobs/{job_id}/queue", response_model=Job)
def update_queue(job_id: int, body: QueueUpdate, session: SessionDep, user: CurrentUser) -> Job:
    """Prioridade e retenção de uma análise em fila (quem a criou, o responsável do caso ou um admin)."""
    job = get_job_or_404(session, job_id)
    if job.created_by != user.username and not can_manage_case(session, user, job.case_id):
        raise HTTPException(403, "Só quem criou a análise, o responsável do processo ou um administrador pode alterar.")
    if job.status != JobStatus.queued:
        raise HTTPException(409, "Só é possível alterar análises em fila.")
    changes = {}
    if body.priority is not None and body.priority != job.priority:
        changes["priority"] = [job.priority, body.priority]
        job.priority = body.priority
    if body.held is not None and body.held != job.held:
        changes["held"] = [job.held, body.held]
        job.held = body.held
    if changes:
        session.add(job)
        session.commit()
        session.refresh(job)
        log_event(session, "job.queue_changed", case_id=job.case_id, actor=user.username,
                  details={"job_id": job.id, "changes": changes})
        runner.wake()
    return job


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: int, body: JobDelete, session: SessionDep, user: CurrentUser) -> None:
    """Elimina uma análise terminada: deteções, tracks e ficheiros gerados (recortes, frames, PNG originais).

    Os vídeos e as imagens de referência do caso não são tocados. A auditoria é append-only: os eventos da
    análise mantêm-se e é acrescentado um "job.deleted" com o resumo da análise eliminada e o motivo.
    """
    actor = user.username
    job = get_job_or_404(session, job_id)
    if job.created_by != actor and not can_manage_case(session, user, job.case_id):
        raise HTTPException(403, "Só quem criou a análise, o responsável do processo ou um administrador a pode "
                                 "eliminar.")
    if body.confirm_job_id != job_id:
        raise HTTPException(422, "Confirmação inválida: indique o número da análise a eliminar.")
    if job.status in (JobStatus.queued, JobStatus.running):
        raise HTTPException(409, "A análise ainda está a decorrer. Cancele-a primeiro.")
    detections = list(session.exec(select(Detection).where(Detection.job_id == job_id)))
    tracks = list(session.exec(select(Track).where(Track.job_id == job_id)))
    summary = {
        "job_id": job_id, "reason": body.reason.strip(), "status": job.status.value,
        "mode": job.params.get("mode", "match"), "params": job.params, "video_ids": job.video_ids,
        "reference_ids": job.reference_ids, "created_by": job.created_by,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None, "message": job.message,
        "detections": len(detections), "tracks": len(tracks), "matches": job.matches, "persons": job.persons,
        "reviewed_detections": sum(1 for d in detections if d.review_status != ReviewStatus.pending),
    }
    saved = list(session.exec(select(SavedFrame).where(SavedFrame.job_id == job_id)))
    summary["saved_frames"] = [{"id": f.id, "sha256": f.sha256} for f in saved]
    alerts = list(session.exec(select(Alert).where(Alert.job_id == job_id)))
    summary["alerts"] = [{"id": a.id, "case_person_id": a.case_person_id, "status": a.status} for a in alerts]
    folder = job_dir(job)
    for row in (*detections, *tracks, *saved, *alerts):
        session.delete(row)
    session.flush()
    session.delete(job)
    session.commit()
    removed = folder.exists()
    shutil.rmtree(folder, ignore_errors=True)
    summary["files_removed"] = removed
    log_event(session, "job.deleted", case_id=job.case_id, actor=actor, details=summary)


@router.get("/jobs/{job_id}/detections", response_model=list[Detection])
def list_detections(
    job_id: int,
    session: SessionDep,
    video_id: int | None = None,
    only_matches: bool = False,
    min_similarity: float | None = None,
    kind: str | None = None,
) -> list[Detection]:
    get_job_or_404(session, job_id)
    q = select(Detection).where(Detection.job_id == job_id)
    if kind is not None:
        q = q.where(Detection.kind == kind)
    if video_id is not None:
        q = q.where(Detection.video_id == video_id)
    if only_matches:
        q = q.where(Detection.is_match == True)  # noqa: E712
    if min_similarity is not None:
        q = q.where(Detection.similarity >= min_similarity)
    return list(session.exec(q.order_by(Detection.video_id, Detection.timestamp_s)))


@router.get("/jobs/{job_id}/segments")
def list_segments(job_id: int, session: SessionDep, gap_s: float | None = None,
                  person_id: int | None = None) -> list[dict]:
    job = get_job_or_404(session, job_id)
    gap = gap_s if gap_s is not None else job.params.get("segment_gap_s", 2.0)
    if job.params.get("mode") == "discover":
        tracks_ = list(session.exec(select(Track).where(Track.job_id == job_id)))
        return person_segments(tracks_, person_id, gap)
    dets = list(session.exec(select(Detection).where(Detection.job_id == job_id)))
    tracks = {t.id: t for t in session.exec(select(Track).where(Track.job_id == job_id))}
    if len(job.params.get("persons") or []) > 1:  # várias pessoas: segmentos de cada uma, com o seu n.º
        out: list[dict] = []
        for pid in [p["id"] for p in job.params["persons"]]:
            if person_id is not None and pid != person_id:
                continue
            mine = [d for d in dets if (d.person_id or getattr(tracks.get(d.track_id), "person_id", None)) == pid]
            out += [{**seg, "person_id": pid} for seg in reports.match_segments(mine, gap, tracks)]
        return sorted(out, key=lambda x: (x["video_id"], x["start_s"]))
    return reports.match_segments(dets, gap, tracks)


# ---------------------------------------------------------------- percurso multi-câmara (hora real)
@router.get("/cases/{case_id}/route")
def case_route(case_id: int, session: SessionDep, job_ids: str | None = None, person_id: int | None = None,
               only: str = "matches") -> dict:
    """Aparições das análises indicadas (por omissão: todas as concluídas) ordenadas pela hora real do DVR, para
    reconstruir o percurso da pessoa entre câmaras. only=confirmed: só segmentos com deteções confirmadas.
    Os segmentos de vídeos sem hora real acertada vão em `unsynced`."""
    get_case_or_404(session, case_id)
    if only not in ("matches", "confirmed"):
        raise HTTPException(422, "only deve ser 'matches' ou 'confirmed'.")
    q = select(Job).where(Job.case_id == case_id, Job.status == JobStatus.completed).order_by(Job.id)
    jobs = list(session.exec(q))
    if job_ids:
        try:
            wanted = {int(x) for x in job_ids.split(",") if x.strip()}
        except ValueError as exc:
            raise HTTPException(422, "job_ids inválido.") from exc
        jobs = [j for j in jobs if j.id in wanted]
    # Todas as câmaras (vídeos) das análises escolhidas, mesmo sem aparições: uma linha por câmara no percurso.
    videos: dict[int, Video | None] = {}
    for job in jobs:
        for vid in job.video_ids or []:
            if vid not in videos:
                videos[vid] = session.get(Video, vid)
    locs = locations_by_id(session, case_id)
    appearances: dict[int, int] = {}
    synced, unsynced = [], []
    for job in jobs:
        discover = job.params.get("mode") == "discover"
        labels = {p["id"]: p["label"] for p in job.params.get("persons") or []}
        segs = list_segments(job.id, session, person_id=person_id if len(jobs) == 1 else None)
        if only == "confirmed" and segs:
            ids = {i for seg in segs for i in seg["detection_ids"]}
            confirmed = {d.id for d in session.exec(select(Detection).where(
                Detection.id.in_(ids), Detection.review_status == ReviewStatus.confirmed))}
            segs = [seg for seg in segs if confirmed & set(seg["detection_ids"])]
        for seg in segs:
            vid = seg["video_id"]
            if vid not in videos:
                videos[vid] = session.get(Video, vid)
            v = videos[vid]
            pid = seg.get("person_id")
            label = (f"Pessoa #{pid}" if discover else labels.get(pid)) if pid else None
            start = real_time(v.dvr_start, v.dvr_rate, v.clock_offset_s, seg["start_s"]) if v else None
            end = real_time(v.dvr_start, v.dvr_rate, v.clock_offset_s, seg["end_s"]) if v else None
            item = {"job_id": job.id, "job_mode": job.params.get("mode", "match"), "person_id": pid,
                    "person_label": label or (job.params.get("persons") or [{}])[0].get("label") or "Pessoa",
                    "video_id": vid, "video_filename": v.filename if v else "",
                    **(video_place(v, locs) if v else {"location_id": None, "location_name": "",
                                                       "camera_name": "", "camera_label": ""}),
                    "start_s": seg["start_s"],
                    "end_s": seg["end_s"], "real_start": start.isoformat() if start else None,
                    "real_end": end.isoformat() if end else None, "evidence": seg.get("evidence", "face"),
                    "best_detection_id": seg.get("best_detection_id"),
                    "best_similarity": seg.get("best_similarity")}
            (synced if start else unsynced).append(item)
            appearances[vid] = appearances.get(vid, 0) + 1
    synced.sort(key=lambda x: (x["real_start"], x["video_id"]))
    unsynced.sort(key=lambda x: (x["video_id"], x["start_s"]))
    return {"segments": synced, "unsynced": unsynced, "jobs": [j.id for j in jobs],
            "videos": [{"id": v.id, "filename": v.filename, **video_place(v, locs), "synced": bool(v.dvr_start),
                        "appearances": appearances.get(v.id, 0)}
                       for v in videos.values() if v is not None],
            "locations": [{"id": loc.id, "name": loc.name} for loc in locs.values()]}


@router.get("/cases/{case_id}/dashboard")
def case_dashboard(case_id: int, session: SessionDep, job_ids: str | None = None) -> dict:
    """Painel do caso: material, análises, progresso da revisão, correspondências por câmara, por hora real e por
    pessoa, e atividade recente. Por omissão considera todas as análises concluídas do caso.

    Correspondência = o mesmo critério do relatório: deteção de face com is_match ou num track com correspondência
    (modo procurar). Vestuário (só pela roupa) e modo descoberta contam à parte."""
    get_case_or_404(session, case_id)
    all_jobs = list(session.exec(select(Job).where(Job.case_id == case_id).order_by(Job.id)))
    jobs = [j for j in all_jobs if j.status == JobStatus.completed]
    if job_ids:
        try:
            wanted = {int(x) for x in job_ids.split(",") if x.strip()}
        except ValueError as exc:
            raise HTTPException(422, "job_ids inválido.") from exc
        jobs = [j for j in jobs if j.id in wanted]
    videos = {v.id: v for v in session.exec(select(Video).where(Video.case_id == case_id).order_by(Video.id))}
    refs = list(session.exec(select(ReferenceImage).where(ReferenceImage.case_id == case_id)))
    n_persons = len(list(session.exec(select(CasePerson.id).where(CasePerson.case_id == case_id))))
    locs = locations_by_id(session, case_id)

    review = {"matches": 0, "confirmed": 0, "rejected": 0, "pending": 0, "clothing": 0,
              "tracks": 0, "tracks_confirmed": 0, "tracks_rejected": 0, "tracks_pending": 0}
    cams: dict[int, dict] = {}
    hours: dict[str, int] = {}
    persons: dict[str, dict] = {}
    pending_by_job: dict[int, int] = {}
    discover = {"jobs": 0, "faces": 0, "persons": 0}
    unsynced = 0

    def cam(vid: int) -> dict:
        if vid not in cams:
            v = videos.get(vid)
            place = video_place(v, locs) if v else {"location_id": None, "location_name": "", "camera_name": f"#{vid}",
                                                    "camera_label": f"#{vid}"}
            cams[vid] = {"video_id": vid, "filename": v.filename if v else f"#{vid}", **place,
                         "synced": bool(v and v.dvr_start),
                         "matches": 0, "confirmed": 0, "rejected": 0, "pending": 0, "clothing": 0,
                         "first": None, "last": None, "first_s": None, "last_s": None, "job_ids": []}
        return cams[vid]

    for job in jobs:
        if (job.params or {}).get("mode") == "discover":
            discover["jobs"] += 1
            discover["faces"] += len(list(session.exec(select(Detection.id).where(Detection.job_id == job.id))))
            discover["persons"] += job.persons or 0
            continue
        labels = {p["id"]: p["label"] for p in (job.params or {}).get("persons") or []}
        multi = len(labels) > 1
        tracks = list(session.exec(select(Track).where(Track.job_id == job.id)))
        match_tracks = {t.id for t in tracks if t.is_match}
        for t in tracks:
            if t.kind == "body":
                if t.evidence == "vestuário":  # aparição só pela roupa (evidência complementar)
                    review["clothing"] += 1
                    cam(t.video_id)["clothing"] += 1
            elif t.is_match:
                review["tracks"] += 1
                review[f"tracks_{t.review_status.value}"] += 1
        rows = session.exec(select(Detection.video_id, Detection.review_status, Detection.timestamp_s,
                                   Detection.track_id, Detection.is_match, Detection.kind, Detection.person_id)
                           .where(Detection.job_id == job.id))
        for vid, status, ts, tid, is_match, kind, pid in rows:
            c = cam(vid)
            if job.id not in c["job_ids"]:
                c["job_ids"].append(job.id)
            if kind == "body":
                continue
            if not (is_match or (tid is not None and tid in match_tracks)):
                continue
            st = status.value if hasattr(status, "value") else str(status)
            review["matches"] += 1
            review[st] += 1
            c["matches"] += 1
            c[st] += 1
            if st == "pending":
                pending_by_job[job.id] = pending_by_job.get(job.id, 0) + 1
            if multi and pid in labels:
                pp = persons.setdefault(labels[pid], {"label": labels[pid], "matches": 0, "confirmed": 0})
                pp["matches"] += 1
                pp["confirmed"] += st == "confirmed"
            c["first_s"] = ts if c["first_s"] is None else min(c["first_s"], ts)
            c["last_s"] = ts if c["last_s"] is None else max(c["last_s"], ts)
            v = videos.get(vid)
            rt = real_time(v.dvr_start, v.dvr_rate, v.clock_offset_s, ts) if v and v.dvr_start else None
            if rt is None:
                unsynced += 1
                continue
            key = rt.strftime("%Y-%m-%d %H")
            hours[key] = hours.get(key, 0) + 1
            iso = rt.isoformat(timespec="seconds")
            c["first"] = iso if c["first"] is None or iso < c["first"] else c["first"]
            c["last"] = iso if c["last"] is None or iso > c["last"] else c["last"]

    # Por local: soma das câmaras de cada local (inclui os locais sem correspondências)
    per_location: dict[int | None, dict] = {
        loc.id: {"location_id": loc.id, "name": loc.name, "cameras": 0, "matches": 0, "confirmed": 0,
                 "rejected": 0, "pending": 0, "clothing": 0} for loc in locs.values()}
    for v in videos.values():
        if v.location_id in per_location:
            per_location[v.location_id]["cameras"] += 1
    for c in cams.values():
        pl = per_location.get(c["location_id"])
        if pl is not None:
            for k in ("matches", "confirmed", "rejected", "pending", "clothing"):
                pl[k] += c[k]

    case_alerts_rows = list(session.exec(select(Alert).where(Alert.case_id == case_id)))
    status_counts: dict[str, int] = {}
    for j in all_jobs:
        status_counts[j.status.value] = status_counts.get(j.status.value, 0) + 1
    recent = session.exec(select(AuditEvent).where(AuditEvent.case_id == case_id)
                          .order_by(AuditEvent.id.desc()).limit(10))
    return {
        "jobs_considered": [j.id for j in jobs],
        "material": {"locations": len(locs), "videos": len(videos),
                     "duration_s": round(sum(v.duration_s for v in videos.values()), 1),
                     "videos_synced": sum(1 for v in videos.values() if v.dvr_start),
                     "references": len(refs), "references_valid": sum(1 for r in refs if r.valid),
                     "persons": n_persons, "size_bytes": disk.case_size(session, case_id)},
        "jobs": {"total": len(all_jobs), "by_status": status_counts},
        "review": review,
        "per_camera": sorted(cams.values(), key=lambda c: (-c["matches"], c["video_id"])),
        "per_location": sorted(per_location.values(), key=lambda x: (-x["matches"], x["location_id"])),
        "per_hour": [{"hour": k, "matches": n} for k, n in sorted(hours.items())],
        "unsynced_matches": unsynced,
        "per_person": sorted(persons.values(), key=lambda p: -p["matches"]),
        "pending_by_job": [{"job_id": k, "pending": n} for k, n in sorted(pending_by_job.items(), key=lambda x: -x[1])],
        "discover": discover,
        "alerts": {"new": sum(1 for a in case_alerts_rows if a.status == "new"),
                   "new_highlighted": sum(1 for a in case_alerts_rows if a.status == "new" and a.highlighted),
                   "total": len(case_alerts_rows)},
        "recent": [{"id": e.id, "timestamp": e.timestamp.isoformat(), "actor": e.actor, "action": e.action}
                   for e in recent],
    }


@router.get("/jobs/{job_id}/tracks", response_model=list[Track])
def list_tracks(job_id: int, session: SessionDep, video_id: int | None = None,
                person_id: int | None = None, kind: str | None = None) -> list[Track]:
    get_job_or_404(session, job_id)
    q = select(Track).where(Track.job_id == job_id)
    if kind is not None:
        q = q.where(Track.kind == kind)
    if video_id is not None:
        q = q.where(Track.video_id == video_id)
    if person_id is not None:
        q = q.where(Track.person_id == person_id)
    return list(session.exec(q.order_by(Track.video_id, Track.start_s)))


# ---------------------------------------------------------------- pessoas (modo descoberta)
def _discover_job(session, job_id: int) -> Job:
    job = get_job_or_404(session, job_id)
    if job.params.get("mode") != "discover":
        raise HTTPException(409, "Esta análise não é do modo descoberta.")
    if job.status in (JobStatus.queued, JobStatus.running):
        raise HTTPException(409, "A análise ainda está a decorrer.")
    return job


def _person_tracks(session, job_id: int, person_id: int) -> list[Track]:
    tracks = list(session.exec(select(Track).where(Track.job_id == job_id, Track.person_id == person_id)))
    if not tracks:
        raise HTTPException(404, f"Pessoa #{person_id} não encontrada nesta análise.")
    return tracks


def _review_tracks(session, tracks: list[Track], body: ReviewUpdate, actor: str) -> list[int]:
    now = utcnow()
    det_ids: list[int] = []
    for t in tracks:
        t.review_status, t.review_note, t.reviewed_by, t.reviewed_at = body.status, body.note, actor, now
        session.add(t)
        for det in session.exec(select(Detection).where(Detection.track_id == t.id)):
            det.review_status, det.review_note, det.reviewed_by, det.reviewed_at = body.status, body.note, actor, now
            session.add(det)
            det_ids.append(det.id)
    return det_ids


@router.get("/jobs/{job_id}/persons")
def list_persons(job_id: int, session: SessionDep) -> list[dict]:
    get_job_or_404(session, job_id)
    return summarize_persons(list(session.exec(select(Track).where(Track.job_id == job_id))))


@router.patch("/jobs/{job_id}/persons/{person_id}/review")
def review_person(job_id: int, person_id: int, body: ReviewUpdate, session: SessionDep, actor: ActorDep) -> dict:
    """Revê uma pessoa inteira: aplica a decisão a todos os seus tracks e deteções."""
    job = _discover_job(session, job_id)
    tracks = _person_tracks(session, job_id, person_id)
    det_ids = _review_tracks(session, tracks, body, actor)
    session.commit()
    log_event(session, "person.reviewed", case_id=job.case_id, actor=actor,
              details={"job_id": job_id, "person_id": person_id, "to": body.status.value, "note": body.note,
                       "tracks": [t.id for t in tracks], "detections": det_ids})
    return next(p for p in summarize_persons(tracks) if p["person_id"] == person_id)


# ---------------------------------------------------------------- alertas (pessoas do processo)
def _alert_out(session, a: Alert, locs: dict, persons: dict) -> dict:
    cp = persons.get(a.case_person_id)
    if cp is None:
        cp = persons[a.case_person_id] = session.get(CasePerson, a.case_person_id)
    ref = session.exec(select(ReferenceImage).where(ReferenceImage.case_person_id == a.case_person_id,
                                                    ReferenceImage.valid == True)  # noqa: E712
                       .order_by(ReferenceImage.id)).first()
    return {**a.model_dump(mode="json"), "case_person_name": cp.name if cp else f"#{a.case_person_id}",
            "reference_id": ref.id if ref else None, "reference_has_crop": bool(ref and ref.face_crop_path),
            "location_names": [locs[i].name for i in a.location_ids if i in locs]}


@router.get("/jobs/{job_id}/alerts")
def job_alerts(job_id: int, session: SessionDep) -> list[dict]:
    job = get_job_or_404(session, job_id)
    locs, persons = locations_by_id(session, job.case_id), {}
    rows = session.exec(select(Alert).where(Alert.job_id == job_id)
                        .order_by(Alert.highlighted.desc(), Alert.distance))
    return [_alert_out(session, a, locs, persons) for a in rows]


@router.get("/cases/{case_id}/alerts")
def case_alerts(case_id: int, session: SessionDep, status: str | None = None) -> list[dict]:
    get_case_or_404(session, case_id)
    q = select(Alert).where(Alert.case_id == case_id)
    if status:
        q = q.where(Alert.status == status)
    locs, persons = locations_by_id(session, case_id), {}
    return [_alert_out(session, a, locs, persons)
            for a in session.exec(q.order_by(Alert.highlighted.desc(), Alert.id.desc()))]


@router.patch("/alerts/{alert_id}")
def review_alert(alert_id: int, body: AlertUpdate, session: SessionDep, actor: ActorDep) -> dict:
    """Confirmar (no modo descoberta confirma também a pessoa descoberta: todos os seus tracks e deteções),
    descartar ou voltar a "novo". Fica na auditoria."""
    a = session.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "Alerta não encontrado.")
    old = a.status
    tracks: list[Track] = []
    if body.status == "confirmed" and a.discovered_person_id is not None:
        tracks = _person_tracks(session, a.job_id, a.discovered_person_id)
        _review_tracks(session, tracks, ReviewUpdate(status=ReviewStatus.confirmed,
                                                     note=body.note or "confirmado pelo alerta"), actor)
    a.status, a.note = body.status, body.note.strip()
    a.reviewed_by, a.reviewed_at = actor, utcnow()
    session.add(a)
    session.commit()
    log_event(session, f"alert.{body.status}" if body.status != "new" else "alert.reopened", case_id=a.case_id,
              actor=actor, details={"alert_id": a.id, "job_id": a.job_id, "case_person_id": a.case_person_id,
                                    "discovered_person_id": a.discovered_person_id, "from": old, "to": a.status,
                                    "note": a.note, "tracks_confirmed": [t.id for t in tracks]})
    return _alert_out(session, a, locations_by_id(session, a.case_id), {})


@router.post("/jobs/{job_id}/persons/merge")
def merge_persons(job_id: int, body: PersonMerge, session: SessionDep, actor: ActorDep) -> list[dict]:
    """Junta pessoas que o agrupamento automático separou (a mesma pessoa vista de ângulos diferentes, etc.)."""
    job = _discover_job(session, job_id)
    ids = sorted(set(body.person_ids))
    if len(ids) < 2:
        raise HTTPException(422, "Indique pelo menos duas pessoas diferentes.")
    target = ids[0]
    moved = []
    for pid in ids[1:]:
        for t in _person_tracks(session, job_id, pid):
            t.person_id = target
            session.add(t)
            moved.append(t.id)
    job.persons = len(_person_ids(session, job_id))
    session.add(job)
    session.commit()
    log_event(session, "person.merged", case_id=job.case_id, actor=actor,
              details={"job_id": job_id, "into": target, "merged": ids[1:], "tracks": moved})
    return list_persons(job_id, session)


def _person_ids(session, job_id: int) -> set[int]:
    return {t.person_id for t in session.exec(select(Track).where(Track.job_id == job_id))
            if t.person_id is not None}


@router.patch("/tracks/{track_id}/person", response_model=Track)
def move_track(track_id: int, body: TrackPerson, session: SessionDep, actor: ActorDep) -> Track:
    """Corrige o agrupamento: passa um track para outra pessoa (ou para uma pessoa nova)."""
    track = session.get(Track, track_id)
    if track is None:
        raise HTTPException(404, "Track não encontrado.")
    job = _discover_job(session, track.job_id)
    existing = _person_ids(session, job.id)
    target = body.person_id if body.person_id is not None else max(existing, default=0) + 1
    if body.person_id is not None and body.person_id not in existing:
        raise HTTPException(404, f"Pessoa #{body.person_id} não encontrada nesta análise.")
    previous = track.person_id
    track.person_id = target
    session.add(track)
    session.flush()
    job.persons = len(_person_ids(session, job.id))
    session.add(job)
    session.commit()
    session.refresh(track)
    log_event(session, "track.person_changed", case_id=job.case_id, actor=actor,
              details={"job_id": job.id, "track_id": track_id, "from": previous, "to": target})
    return track


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


def _download_name(det: Detection, job: Job, suffix: str) -> str:
    ts = format_timestamp(det.timestamp_s).replace(":", "-")
    return (f"faceai_caso{job.case_id}_analise{job.id}_video{det.video_id}_frame{det.frame_index:07d}_{ts}"
            f"_det{det.id}_{suffix}")


def _log_download(session, det: Detection, job: Job, actor: str, kind: str, body: bytes, filename: str,
                  **extra) -> None:
    log_event(session, "detection.frame_downloaded", case_id=job.case_id, actor=actor,
              details={"job_id": job.id, "detection_id": det.id, "video_id": det.video_id,
                       "frame_index": det.frame_index, "timestamp_s": det.timestamp_s, "kind": kind,
                       "filename": filename, "size_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                       **extra})


@router.get("/detections/{det_id}/frame/original")
def detection_original_frame(det_id: int, session: SessionDep, actor: ActorDep, download: bool = False,
                             ) -> Response:
    """Frame completo do vídeo original, sem anotações, em PNG (sem perdas em relação ao frame descodificado)."""
    det = session.get(Detection, det_id)
    if det is None:
        raise HTTPException(404, "Deteção não encontrada.")
    job = session.get(Job, det.job_id)
    video = session.get(Video, det.video_id)
    if job is None or video is None or not Path(video.stored_path).exists():
        raise HTTPException(404, "O vídeo original desta deteção já não está disponível.")
    try:
        body, method, err = validated_frames.original_frame_png(job, video, det)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    headers = {"X-FaceAI-Frame-Index": str(det.frame_index), "X-FaceAI-Extraction": method}
    if err is not None:
        headers["X-FaceAI-Crop-Error"] = str(err)
    if download:
        filename = _download_name(det, job, "original.png")
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        _log_download(session, det, job, actor, "original", body, filename,
                      video_filename=video.filename, extraction=method, crop_error=err)
    return Response(content=body, media_type="image/png", headers=headers)


@router.get("/detections/{det_id}/enhanced")
def detection_enhanced(det_id: int, session: SessionDep, actor: ActorDep, src: str = "crop", ops: str = "",
                       upscale: int = 1, gamma: float = 1.0, download: bool = False) -> Response:
    """Imagem melhorada SÓ para visualização (contraste, nitidez, ruído, gama, ampliação Lanczos).

    src=crop (recorte da face) ou original (frame completo do vídeo, sem anotações). O original não é alterado.
    Com download=1 a imagem leva uma faixa "MELHORADA" e o download fica na auditoria (operações e SHA-256)."""
    det = session.get(Detection, det_id)
    if det is None or src not in ("crop", "original"):
        raise HTTPException(404, "Imagem não encontrada.")
    try:
        e = enhance.Enhancement.parse(ops, upscale, gamma)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    job = session.get(Job, det.job_id)
    if src == "crop":
        if not Path(det.crop_path).exists():
            raise HTTPException(404, "Recorte indisponível.")
        img = read_image(det.crop_path)
    else:
        video = session.get(Video, det.video_id)
        if job is None or video is None or not Path(video.stored_path).exists():
            raise HTTPException(404, "O vídeo original desta deteção já não está disponível.")
        try:
            png, _, _ = validated_frames.original_frame_png(job, video, det)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    out = enhance.apply(img, e)
    if download:
        out = enhance.watermark(out, e)
    ok, buf = cv2.imencode(".png", out)
    if not ok:
        raise HTTPException(500, "Falha ao codificar a imagem.")
    body = buf.tobytes()
    headers = {"X-FaceAI-Enhancement": e.describe(), "Cache-Control": "private, max-age=300"}
    if download and job is not None:
        filename = _download_name(det, job, f"{'face' if src == 'crop' else 'original'}_melhorada.png")
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        log_event(session, "detection.enhanced_downloaded", case_id=job.case_id, actor=actor,
                  details={"job_id": job.id, "detection_id": det.id, "source": src, "enhancement": e.as_dict(),
                           "filename": filename, "size_bytes": len(body),
                           "sha256": hashlib.sha256(body).hexdigest()})
    return Response(content=body, media_type="image/png", headers=headers)


@router.get("/detections/{det_id}/{kind}")
def detection_image(det_id: int, kind: str, session: SessionDep, actor: ActorDep, download: bool = False,
                    ) -> Response:
    det = session.get(Detection, det_id)
    if det is None or kind not in ("crop", "frame"):
        raise HTTPException(404, "Imagem não encontrada.")
    path = det.crop_path if kind == "crop" else det.frame_path
    if not download:
        return FileResponse(path, media_type="image/jpeg")
    job = session.get(Job, det.job_id)
    if job is None or not Path(path).exists():
        raise HTTPException(404, "Imagem não encontrada.")
    body = Path(path).read_bytes()
    filename = _download_name(det, job, "anotado.jpg" if kind == "frame" else "face.jpg")
    _log_download(session, det, job, actor,
                  "anotado" if kind == "frame" else "recorte", body, filename)
    return Response(content=body, media_type="image/jpeg",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/detections/{det_id}/as-reference", response_model=ReferenceImage, status_code=201)
def detection_as_reference(det_id: int, session: SessionDep, actor: ActorDep,
                           case_person_id: int | None = None) -> ReferenceImage:
    """Usa a face de uma deteção como nova imagem de referência do caso, opcionalmente já numa pessoa do caso
    ("Adicionar esta face à pessoa A"; ex.: faces confirmadas do próprio CCTV)."""
    det = session.get(Detection, det_id)
    if det is None:
        raise HTTPException(404, "Deteção não encontrada.")
    if det.kind == "body":
        raise HTTPException(422, "Só uma face pode ser usada como imagem de referência (esta deteção é de corpo).")
    job = session.get(Job, det.job_id)
    if job is None or not Path(det.crop_path).exists():
        raise HTTPException(404, "O recorte desta deteção já não está disponível.")
    person = None
    if case_person_id is not None:
        person = session.get(CasePerson, case_person_id)
        if person is None or person.case_id != job.case_id:
            raise HTTPException(422, "Pessoa inexistente neste processo.")
    crop = read_image(det.crop_path)
    scale = 160 / min(crop.shape[:2])
    if scale > 1:  # faces pequenas de CCTV: ampliar ajuda o detetor a validá-la como referência
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    dest = settings.case_dir(job.case_id) / "references" / f"{uuid.uuid4().hex}.jpg"
    write_jpeg(dest, crop, quality=95)
    track = session.get(Track, det.track_id) if det.track_id is not None else None
    name = (f"analise{job.id}_pessoa{track.person_id}_det{det.id}.jpg" if track and track.person_id
            else f"analise{job.id}_det{det.id}.jpg")
    ref = register_reference(session, job.case_id, dest, name, dest.stat().st_size, actor)
    ref.source_detection_id, ref.case_person_id = det.id, case_person_id
    session.add(ref)
    session.commit()
    session.refresh(ref)
    log_event(session, "reference.from_detection", case_id=job.case_id, actor=actor,
              details={"reference_id": ref.id, "detection_id": det.id, "job_id": job.id,
                       "person_id": track.person_id if track else None, "valid": ref.valid,
                       "case_person_id": case_person_id, "case_person": person.name if person else None,
                       "note": "recorte da face (ampliado se pequeno), não é uma imagem original"})
    return ref


EXPORTS = {
    "html": ("text/html; charset=utf-8", "html"),
    "pdf": ("application/pdf", "pdf"),
    "csv": ("text/csv; charset=utf-8", "csv"),
    "json": ("application/json", "json"),
    "zip": ("application/zip", "zip"),
}


# ---------------------------------------------------------------- ligar pessoas entre análises
def _similar(session, user, job: Job, query, *, source: dict, threshold: float | None, scope: str,
             limit: int) -> dict:
    if query is None:
        raise HTTPException(404, "Sem embedding guardado para comparar (análises anteriores a esta versão não "
                                 "guardavam os embeddings dos tracks no modo procurar).")
    if scope not in ("case", "all"):
        raise HTTPException(422, "scope deve ser 'case' ou 'all'.")
    emb, model = query
    th = float(threshold if threshold is not None else job.params.get("threshold") or 0.5)
    if not 0 < th <= 1.5:
        raise HTTPException(422, "Limiar inválido.")
    case_ids = {job.case_id} if scope == "case" else accessible_case_ids(session, user)
    out = linking.find_similar(session, emb, model, case_ids=case_ids, exclude_job_id=job.id, threshold=th,
                               limit=max(1, min(limit, 500)))
    log_event(session, "person.linked_search", case_id=job.case_id, actor=user.username,
              details={**source, "job_id": job.id, "threshold": th, "scope": scope,
                       "results": len(out["results"]),
                       "cases_with_results": sorted({r["case_id"] for r in out["results"]})})
    return out


@router.get("/tracks/{track_id}/similar")
def similar_to_track(track_id: int, session: SessionDep, user: CurrentUser, threshold: float | None = None,
                     scope: str = "all", limit: int = 100) -> dict:
    """Tracks parecidos noutras análises dos casos acessíveis (scope=case: só o mesmo caso)."""
    track = session.get(Track, track_id)
    job = get_job_or_404(session, track.job_id) if track else None
    if job is None:
        raise HTTPException(404, "Track não encontrado.")
    return _similar(session, user, job, linking.track_query(job, track_id), source={"track_id": track_id},
                    threshold=threshold, scope=scope, limit=limit)


@router.get("/jobs/{job_id}/persons/{person_id}/similar")
def similar_to_person(job_id: int, person_id: int, session: SessionDep, user: CurrentUser,
                      threshold: float | None = None, scope: str = "all", limit: int = 100) -> dict:
    """Como /tracks/{id}/similar, com a média dos tracks da pessoa (descoberta ou várias pessoas procuradas)."""
    job = get_job_or_404(session, job_id)
    return _similar(session, user, job, linking.person_query(job, person_id), source={"person_id": person_id},
                    threshold=threshold, scope=scope, limit=limit)


# ---------------------------------------------------------------- pesquisa por foto noutros casos
def _photo_search(session, user, case_id: int, refs: list[ReferenceImage], *, source: dict, scope: str,
                  threshold: float | None, limit: int) -> dict:
    if scope not in ("case", "all"):
        raise HTTPException(422, "scope deve ser 'case' ou 'all'.")
    if threshold is not None and not 0 < threshold <= 1.5:
        raise HTTPException(422, "Limiar inválido.")
    refs = [r for r in refs if r.valid]
    if not refs:
        raise HTTPException(422, "Sem fotos de referência válidas para procurar.")
    case_ids = {case_id} if scope == "case" else accessible_case_ids(session, user)
    out = linking.search_by_references(session, refs, get_engine(), case_ids=case_ids, threshold=threshold,
                                       limit=max(1, min(limit, 500)))
    log_event(session, "person.photo_search", case_id=case_id, actor=user.username,
              details={**source, "reference_ids": [r.id for r in refs], "scope": scope, "threshold": threshold,
                       "thresholds_by_job": out["thresholds_by_job"], "models": out["model"],
                       "jobs_searched": out["jobs_searched"], "jobs_not_searched": out["jobs_not_searched"],
                       "results": len(out["results"]),
                       "cases_with_results": sorted({r["case_id"] for r in out["results"]})})
    return out


@router.get("/case-persons/{case_person_id}/search")
def search_case_person(case_person_id: int, session: SessionDep, user: CurrentUser, scope: str = "all",
                       threshold: float | None = None, limit: int = 100) -> dict:
    """Procura a pessoa (todas as suas fotos válidas) nas análises já feitas dos casos acessíveis, sem reprocessar
    vídeo. threshold vazio = o limiar de cada análise."""
    person = session.get(CasePerson, case_person_id)
    if person is None:
        raise HTTPException(404, "Pessoa não encontrada.")
    refs = list(session.exec(select(ReferenceImage).where(ReferenceImage.case_person_id == person.id)
                             .order_by(ReferenceImage.id)))
    return _photo_search(session, user, person.case_id, refs, source={"case_person_id": person.id,
                         "name": person.name}, scope=scope, threshold=threshold, limit=limit)


@router.get("/references/{ref_id}/search")
def search_reference(ref_id: int, session: SessionDep, user: CurrentUser, scope: str = "all",
                     threshold: float | None = None, limit: int = 100) -> dict:
    """Como /case-persons/{id}/search, só com esta foto."""
    ref = session.get(ReferenceImage, ref_id)
    if ref is None:
        raise HTTPException(404, "Referência não encontrada.")
    return _photo_search(session, user, ref.case_id, [ref], source={"reference_id": ref.id},
                         scope=scope, threshold=threshold, limit=limit)


@router.get("/jobs/{job_id}/frames/validated.zip")
def job_validated_frames(job_id: int, session: SessionDep, actor: ActorDep) -> StreamingResponse:
    """ZIP com os frames originais de todas as deteções confirmadas da análise (+ manifesto com SHA-256)."""
    job = get_job_or_404(session, job_id)
    return validated_zip_response(session, [job], case_id=job.case_id, actor=actor, scope=f"análise {job.id}",
                                  filename=f"faceai_caso{job.case_id}_analise{job.id}_frames_validados")


@router.get("/cases/{case_id}/frames/validated")
def case_validated_summary(case_id: int, session: SessionDep) -> dict:
    """Quantas deteções confirmadas (e frames distintos) iriam para o ZIP do caso."""
    get_case_or_404(session, case_id)
    jobs = list(session.exec(select(Job).where(Job.case_id == case_id)))
    validated = validated_frames.collect(session, jobs)
    items = validated.items
    return {"detections": len(items), "frames": len({(j.id, v.id, d.frame_index) for d, j, v in items}),
            "saved_frames": len(validated.frames)}


def _validated_label(job: Job, pid: int | None) -> str:
    """Pessoa de uma deteção confirmada: o nome procurado ou, no modo descoberta, "Pessoa #n"."""
    params = job.params or {}
    if params.get("mode") == "discover":
        return f"Pessoa #{pid}" if pid else ""
    persons = params.get("persons") or []
    if len(persons) == 1:
        return persons[0].get("label") or ""
    return next((p["label"] for p in persons if p["id"] == pid), "")


@router.get("/cases/{case_id}/frames/validated/items")
def case_validated_items(case_id: int, session: SessionDep) -> list[dict]:
    """Deteções confirmadas do caso (as que vão para o ZIP de frames validados), com local, câmara e hora."""
    get_case_or_404(session, case_id)
    jobs = list(session.exec(select(Job).where(Job.case_id == case_id).order_by(Job.id)))
    validated = validated_frames.collect(session, jobs)
    track_ids = {d.track_id for d, _, _ in validated.items if d.track_id is not None}
    tracks = {t.id: t for t in session.exec(select(Track).where(Track.id.in_(track_ids or {-1})))}
    out = []
    for det, job, video in validated.items:
        t = tracks.get(det.track_id) if det.track_id is not None else None
        pid = det.person_id or (t.person_id if t is not None else None)
        rt = real_time(video.dvr_start, video.dvr_rate, video.clock_offset_s, det.timestamp_s)
        out.append({
            "detection_id": det.id, "job_id": job.id, "job_mode": (job.params or {}).get("mode", "match"),
            "video_id": video.id, "video_filename": video.filename, **validated.places.get(video.id, {}),
            "frame_index": det.frame_index, "timestamp_s": det.timestamp_s,
            "real_time": rt.isoformat(timespec="milliseconds") if rt else None,
            "kind": det.kind or "face", "evidence": t.evidence if t is not None and t.kind == "body" else "face",
            "person_id": pid, "person_label": _validated_label(job, pid), "similarity": det.similarity,
            "reviewed_by": det.reviewed_by, "reviewed_at": det.reviewed_at.isoformat() if det.reviewed_at else None,
            "review_note": det.review_note, "type": "detection", "saved_frame_id": None,
        })
    for sf, job, video in validated.frames:
        rt = real_time(video.dvr_start, video.dvr_rate, video.clock_offset_s, sf.timestamp_s)
        out.append({
            "detection_id": None, "saved_frame_id": sf.id, "type": "saved_frame", "job_id": job.id,
            "job_mode": (job.params or {}).get("mode", "match"), "video_id": video.id,
            "video_filename": video.filename, **validated.places.get(video.id, {}),
            "frame_index": sf.frame_index, "timestamp_s": sf.timestamp_s,
            "real_time": rt.isoformat(timespec="milliseconds") if rt else None, "kind": "frame",
            "evidence": "frame guardado", "person_id": None, "person_label": "", "similarity": None,
            "reviewed_by": sf.created_by, "reviewed_at": sf.created_at.isoformat() if sf.created_at else None,
            "review_note": sf.note,
        })
    out.sort(key=lambda x: (x["real_time"] is None, x["real_time"] or "", x["video_id"], x["timestamp_s"]))
    return out


@router.post("/jobs/{job_id}/saved-frames", status_code=201)
def save_frame(job_id: int, body: SavedFrameCreate, session: SessionDep, actor: ActorDep) -> dict:
    """Guarda o frame original (PNG, sem anotações) no instante do leitor; fica nos resultados como validado."""
    job = get_job_or_404(session, job_id)
    if body.video_id not in (job.video_ids or []):
        raise HTTPException(422, "O vídeo não pertence a esta análise.")
    video = session.get(Video, body.video_id)
    if video is None or not Path(video.stored_path).exists():
        raise HTTPException(404, "O vídeo original já não está disponível.")
    last = max(0, (video.frame_count or 1) - 1)
    frame_index = min(last, max(0, int(round(body.t * (video.fps or 0)))))
    try:
        image = extract_frame(video.stored_path, frame_index, None, (0, 0, 0, 0)).image
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise HTTPException(500, "Falha ao codificar o frame.")
    png = buf.tobytes()
    sf = SavedFrame(case_id=job.case_id, job_id=job.id, video_id=video.id, frame_index=frame_index,
                    timestamp_s=round(frame_index / video.fps, 3) if video.fps else body.t, stored_path="",
                    sha256=hashlib.sha256(png).hexdigest(), size_bytes=len(png), width=image.shape[1],
                    height=image.shape[0], note=body.note.strip(), created_by=actor)
    session.add(sf)
    session.flush()
    dest = job_dir(job) / "saved" / f"frame{sf.id:05d}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(png)
    sf.stored_path = str(dest)
    session.add(sf)
    session.commit()
    session.refresh(sf)
    log_event(session, "frame.saved", case_id=job.case_id, actor=actor,
              details={"saved_frame_id": sf.id, "job_id": job.id, "video_id": video.id, "filename": video.filename,
                       "frame_index": frame_index, "timestamp_s": sf.timestamp_s, "sha256": sf.sha256,
                       "size_bytes": sf.size_bytes, "note": sf.note})
    return sf.model_dump(mode="json", exclude={"stored_path"})


@router.get("/saved-frames/{saved_frame_id}/image")
def saved_frame_image(saved_frame_id: int, session: SessionDep, actor: ActorDep, download: bool = False):
    sf = session.get(SavedFrame, saved_frame_id)
    if sf is None or not Path(sf.stored_path).exists():
        raise HTTPException(404, "Frame não encontrado.")
    if not download:
        return FileResponse(sf.stored_path, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})
    filename = f"faceai_caso{sf.case_id}_analise{sf.job_id}_frame_guardado{sf.id}.png"
    log_event(session, "frame.saved_downloaded", case_id=sf.case_id, actor=actor,
              details={"saved_frame_id": sf.id, "job_id": sf.job_id, "filename": filename, "sha256": sf.sha256})
    return FileResponse(sf.stored_path, media_type="image/png", filename=filename)


@router.delete("/saved-frames/{saved_frame_id}", status_code=204)
def delete_saved_frame(saved_frame_id: int, session: SessionDep, user: CurrentUser) -> None:
    """Retira um frame guardado (quem o guardou, o responsável do caso ou um administrador)."""
    sf = session.get(SavedFrame, saved_frame_id)
    if sf is None:
        raise HTTPException(404, "Frame não encontrado.")
    if sf.created_by != user.username and not can_manage_case(session, user, sf.case_id):
        raise HTTPException(403, "Só quem guardou o frame, o responsável do processo ou um administrador o pode "
                                 "retirar.")
    details = {"saved_frame_id": sf.id, "job_id": sf.job_id, "video_id": sf.video_id, "frame_index": sf.frame_index,
               "sha256": sf.sha256, "note": sf.note, "created_by": sf.created_by}
    Path(sf.stored_path).unlink(missing_ok=True)
    session.delete(sf)
    session.commit()
    log_event(session, "frame.saved_deleted", case_id=sf.case_id, actor=user.username, details=details)


@router.get("/cases/{case_id}/frames/validated.zip")
def case_validated_frames(case_id: int, session: SessionDep, actor: ActorDep, detection_ids: str | None = None,
                          frame_ids: str | None = None) -> StreamingResponse:
    """ZIP com os frames originais das deteções confirmadas e dos frames guardados de todas as análises do caso
    (ou só dos indicados em detection_ids / frame_ids)."""
    get_case_or_404(session, case_id)
    selection = None
    if detection_ids is not None or frame_ids is not None:
        selection = (_id_set(detection_ids, "detection_ids"), _id_set(frame_ids, "frame_ids"))
    jobs = list(session.exec(select(Job).where(Job.case_id == case_id).order_by(Job.id)))
    return validated_zip_response(session, jobs, case_id=case_id, actor=actor,
                                  scope=f"processo {case_id}" + (" (seleção)" if selection else ""),
                                  filename=f"faceai_caso{case_id}_frames_validados", selection=selection)


def _id_set(raw: str | None, name: str) -> set[int]:
    try:
        return {int(x) for x in (raw or "").split(",") if x.strip()}
    except ValueError as exc:
        raise HTTPException(422, f"{name} inválido.") from exc


def validated_zip_response(session, jobs: list[Job], *, case_id: int, actor: str, scope: str, filename: str,
                           selection: tuple[set[int], set[int]] | None = None) -> StreamingResponse:
    validated = validated_frames.collect(session, jobs)
    if selection is not None:  # seleção: só itens deste caso (ids alheios são ignorados)
        validated.items = [it for it in validated.items if it[0].id in selection[0]]
        validated.frames = [it for it in validated.frames if it[0].id in selection[1]]
    if not validated.items and not validated.frames:
        raise HTTPException(404, "Não há deteções confirmadas: valide primeiro as deteções que quer exportar.")
    log_event(session, "frames.validated_exported", case_id=case_id, actor=actor,
              details={"scope": scope, "jobs": [j.id for j in jobs], "detections": validated.detection_ids,
                       "saved_frames": [f.id for f, _, _ in validated.frames]})
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(validated_frames.stream_zip(validated, scope=scope, actor=actor),
                             media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{filename}_{stamp}.zip"'})


@router.get("/jobs/{job_id}/export/{fmt}")
def export_job(job_id: int, fmt: str, session: SessionDep, actor: ActorDep) -> Response:
    if fmt not in EXPORTS:
        raise HTTPException(404, f"Formato desconhecido: {fmt}")
    job = get_job_or_404(session, job_id)
    if job.status in (JobStatus.queued, JobStatus.running):
        raise HTTPException(409, "A análise ainda está a decorrer.")
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
