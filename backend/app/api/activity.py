"""Tarefas em curso (faixa no topo da interface): análises, conversões de reprodução e uploads.

Só mostra o que pertence a casos a que o utilizador tem acesso.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter
from sqlmodel import select

from ..models import Case, Job, JobStatus, Upload, UserRole, Video
from ..services import disk
from ..services.jobs import queue_positions
from ..services.transcode import PENDING, RUNNING
from .deps import CurrentUser, SessionDep, accessible_case_ids

router = APIRouter(prefix="/api", tags=["sistema"])

UPLOAD_ACTIVE_SECONDS = 120  # um upload sem blocos novos há mais tempo do que isto considera-se parado


@router.get("/activity")
def activity(session: SessionDep, user: CurrentUser) -> dict:
    allowed = accessible_case_ids(session, user)

    def visible(q, col):
        return q if allowed is None else q.where(col.in_(allowed or [-1]))

    names: dict[int, str] = {}

    def case_name(case_id: int) -> str:
        if case_id not in names:
            c = session.get(Case, case_id)
            names[case_id] = c.name if c else ""
        return names[case_id]

    jobs = session.exec(visible(select(Job).where(Job.status.in_([JobStatus.queued, JobStatus.running])),
                                Job.case_id).order_by(Job.status.desc(), Job.priority.desc(), Job.id))
    positions = queue_positions(session)
    conversions = session.exec(visible(select(Video).where(Video.playback_status.in_([PENDING, RUNNING])),
                                       Video.case_id).order_by(Video.id))
    uploads = []
    now = time.time()
    for u in session.exec(visible(select(Upload).where(Upload.completed_at == None), Upload.case_id)):  # noqa: E711
        try:
            path = Path(u.temp_path)
            if now - path.stat().st_mtime > UPLOAD_ACTIVE_SECONDS:
                continue
            received = path.stat().st_size
        except OSError:
            continue
        uploads.append({"id": u.id, "case_id": u.case_id, "case_name": case_name(u.case_id), "kind": u.kind,
                        "filename": u.filename, "received_bytes": received, "size_bytes": u.size_bytes,
                        "created_by": u.created_by})
    st = disk.disk_status()
    disk.note_alert(session, st)
    return {
        "disk": st if user.role == UserRole.admin else {k: st[k] for k in ("level", "percent", "free")},
        "jobs": [{"id": j.id, "case_id": j.case_id, "case_name": case_name(j.case_id), "status": j.status,
                  "mode": (j.params or {}).get("mode", "search"), "progress": j.progress, "eta_s": j.eta_s,
                  "message": j.message, "created_by": j.created_by, "started_at": j.started_at,
                  "priority": j.priority, "held": j.held, "queue_position": positions.get(j.id)} for j in jobs],
        "conversions": [{"video_id": v.id, "case_id": v.case_id, "case_name": case_name(v.case_id),
                         "filename": v.filename, "status": v.playback_status, "progress": v.playback_progress}
                        for v in conversions],
        "uploads": uploads,
    }
