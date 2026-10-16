"""Casos, imagens de referência, vídeos e auditoria."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import select

from ..config import settings
from ..models import (
    AuditEvent,
    Case,
    CaseMember,
    CasePerson,
    Detection,
    Job,
    JobStatus,
    Location,
    ReferenceImage,
    Track,
    Upload,
    User,
    Video,
)
from ..schemas import (
    CaseCreate,
    CaseDelete,
    CaseOut,
    CasePersonCreate,
    CasePersonUpdate,
    CaseUpdate,
    LocationCreate,
    MemberAdd,
    ReferenceUpdate,
    VideoDelete,
    VideoUpdate,
)
from ..services import disk
from ..services.audit import log_event
from ..services.clock_auto import request_auto_clock
from ..services.face_engine import get_engine
from ..services.locations import default_location
from ..services.references import validate_reference
from ..services.transcode import (
    FORMATS,
    READY,
    cancel_copies,
    probe_stream,
    request_case_copies,
    request_playback_copy,
)
from ..services.video_processor import probe
from .deps import (
    ActorDep,
    CurrentUser,
    SessionDep,
    accessible_case_ids,
    authorize_objects,
    can_manage_case,
    get_case_or_404,
    membership,
)

# Todas as rotas exigem sessão; os objetos do caminho têm de pertencer a um caso acessível (ver deps).
router = APIRouter(prefix="/api", tags=["casos"], dependencies=[Depends(authorize_objects)])

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXT = {
    ".mp4", ".avi", ".mkv", ".mov", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts",
    ".dav", ".h264", ".264", ".webm", ".flv", ".3gp",
}


def _store_upload(upload: UploadFile, dest_dir: Path, allowed: set[str]) -> tuple[Path, int]:
    original = Path(upload.filename or "ficheiro").name
    ext = Path(original).suffix.lower()
    if ext not in allowed:
        raise HTTPException(415, f"Extensão não suportada: '{ext or original}'.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}{ext}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(upload.file, fh, length=4 * 1024 * 1024)
    return dest, dest.stat().st_size


def register_reference(session, case_id: int, path: Path, filename: str, size: int, actor: str) -> ReferenceImage:
    """Regista uma imagem de referência já gravada em disco e valida-a (exatamente uma face)."""
    engine = get_engine()
    ref = ReferenceImage(case_id=case_id, filename=filename, stored_path=str(path), size_bytes=size)
    session.add(ref)
    session.commit()
    session.refresh(ref)
    try:
        validate_reference(ref, engine, model=engine.models()[0].name, detector=engine.detectors()[0],
                           crop_dir=path.parent)
    except Exception as exc:
        ref.valid, ref.message = False, f"Erro ao processar a imagem: {exc}"
    session.add(ref)
    session.commit()
    session.refresh(ref)
    log_event(session, "reference.uploaded", case_id=case_id, actor=actor,
              details={"reference_id": ref.id, "filename": ref.filename, "size_bytes": size,
                       "face_count": ref.face_count, "valid": ref.valid, "message": ref.message})
    return ref


def location_of_case(session, case_id: int, location_id: int | None, actor: str = "") -> Location:
    """Local de destino de um vídeo: o indicado (tem de ser do caso) ou, sem indicação, o "Geral" do caso."""
    if location_id is None:
        loc = default_location(session, case_id, actor)
        session.commit()
        return loc
    loc = session.get(Location, location_id)
    if loc is None or loc.case_id != case_id:
        raise HTTPException(422, "Local inexistente neste caso.")
    return loc


def register_video(session, case_id: int, path: Path, filename: str, size: int, actor: str,
                   location_id: int | None = None) -> Video:
    """Regista um vídeo já gravado em disco (metadados via OpenCV) e pede a cópia de reprodução, se útil."""
    try:
        loc = location_of_case(session, case_id, location_id, actor)
        info = probe(path)
    except (ValueError, HTTPException) as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, f"{filename}: {exc}") from exc
    video = Video(case_id=case_id, location_id=loc.id, filename=filename, stored_path=str(path), size_bytes=size,
                  duration_s=round(info.duration_s, 3), fps=round(info.fps, 3), frame_count=info.frame_count,
                  width=info.width, height=info.height, codec=info.codec)
    session.add(video)
    session.commit()
    session.refresh(video)
    log_event(session, "video.uploaded", case_id=case_id, actor=actor,
              details={**video.model_dump(mode="json", exclude={"stored_path", "playback_path"}),
                       "location_name": loc.name})
    request_playback_copy(session, video)
    request_auto_clock(session, video)
    return video


# ---------------------------------------------------------------- casos
@router.post("/cases", response_model=Case, status_code=201)
def create_case(body: CaseCreate, session: SessionDep, user: CurrentUser) -> Case:
    actor = user.username
    case = Case(name=body.name.strip(), description=body.description, analyst=user.display_name or actor)
    session.add(case)
    session.commit()
    session.refresh(case)
    session.add(CaseMember(case_id=case.id, user_id=user.id, role="owner", added_by=actor))
    session.commit()
    settings.case_dir(case.id).mkdir(parents=True, exist_ok=True)
    log_event(session, "case.created", case_id=case.id, actor=actor or case.analyst,
              details=body.model_dump())
    return case


@router.get("/cases", response_model=list[CaseOut])
def list_cases(session: SessionDep, user: CurrentUser) -> list[CaseOut]:
    """Só os casos a que o utilizador tem acesso (o administrador vê todos), com o espaço ocupado em disco."""
    allowed = accessible_case_ids(session, user)
    q = select(Case).order_by(Case.id.desc())
    if allowed is not None:
        q = q.where(Case.id.in_(allowed or [-1]))
    return [CaseOut(**c.model_dump(), size_bytes=disk.case_size(session, c.id)) for c in session.exec(q)]


@router.get("/cases/{case_id}", response_model=Case)
def get_case(case_id: int, session: SessionDep) -> Case:
    return get_case_or_404(session, case_id)


@router.patch("/cases/{case_id}", response_model=Case)
def update_case(case_id: int, body: CaseUpdate, session: SessionDep, user: CurrentUser) -> Case:
    """Altera o nome e/ou a descrição do caso (responsável do caso ou administrador). Os valores anteriores ficam
    na auditoria."""
    actor = user.username
    case = get_case_or_404(session, case_id)
    _require_manager(session, user, case_id)
    changes = {}
    if body.name is not None and body.name.strip() != case.name:
        if not body.name.strip():
            raise HTTPException(422, "O nome do caso não pode ficar vazio.")
        changes["name"] = {"from": case.name, "to": body.name.strip()}
        case.name = body.name.strip()
    if body.description is not None and body.description != case.description:
        changes["description"] = {"from": case.description, "to": body.description}
        case.description = body.description
    if changes:
        session.add(case)
        session.commit()
        session.refresh(case)
        log_event(session, "case.updated", case_id=case_id, actor=actor, details=changes)
    return case


@router.delete("/cases/{case_id}", status_code=204)
def delete_case(case_id: int, body: CaseDelete, session: SessionDep, user: CurrentUser) -> None:
    """Elimina o caso e tudo o que lhe pertence: vídeos originais e cópias de reprodução, imagens de referência,
    análises (deteções, tracks, recortes, frames) e uploads por concluir.

    A auditoria é append-only: os eventos do caso mantêm-se (base de dados e audit.jsonl) e é acrescentado um
    "case.deleted" com o resumo do que foi apagado e o motivo.
    """
    actor = user.username
    case = get_case_or_404(session, case_id)
    _require_manager(session, user, case_id)
    if body.confirm_case_id != case_id:
        raise HTTPException(422, "Confirmação inválida: indique o número do caso a eliminar.")
    jobs = list(session.exec(select(Job).where(Job.case_id == case_id)))
    if any(j.status in (JobStatus.queued, JobStatus.running) for j in jobs):
        raise HTTPException(409, "Há análises a decorrer neste caso. Cancele-as primeiro.")
    videos = list(session.exec(select(Video).where(Video.case_id == case_id)))
    refs = list(session.exec(select(ReferenceImage).where(ReferenceImage.case_id == case_id)))
    uploads = list(session.exec(select(Upload).where(Upload.case_id == case_id)))
    cancel_copies([v.id for v in videos])

    job_ids = [j.id for j in jobs] or [-1]
    detections = list(session.exec(select(Detection).where(Detection.job_id.in_(job_ids))))
    tracks = list(session.exec(select(Track).where(Track.job_id.in_(job_ids))))
    files = [Path(p) for v in videos for p in (v.stored_path, v.playback_path) if p]
    files += [Path(p) for r in refs for p in (r.stored_path, r.face_crop_path) if p]
    files += [Path(u.temp_path) for u in uploads]
    summary = {
        "case_id": case_id, "name": case.name, "description": case.description, "reason": body.reason.strip(),
        "videos": [{"id": v.id, "filename": v.filename, "size_bytes": v.size_bytes,
                    "playback_copy": bool(v.playback_path)} for v in videos],
        "references": [{"id": r.id, "filename": r.filename} for r in refs],
        "jobs": [j.id for j in jobs], "detections": len(detections), "tracks": len(tracks),
        "pending_uploads": len(uploads),
    }
    members = list(session.exec(select(CaseMember).where(CaseMember.case_id == case_id)))
    summary["members"] = [m.user_id for m in members]
    persons = list(session.exec(select(CasePerson).where(CasePerson.case_id == case_id)))
    summary["persons"] = [p.name for p in persons]
    locations = list(session.exec(select(Location).where(Location.case_id == case_id)))
    summary["locations"] = [loc.name for loc in locations]
    for rows in (detections, tracks, jobs, uploads, refs, persons, videos, locations, members):
        for row in rows:
            session.delete(row)
        session.flush()
    session.delete(case)
    session.commit()

    # Ficheiros: tudo o que está na pasta do caso + qualquer ficheiro do caso guardado fora dela.
    removed_bytes = 0
    for f in files:
        try:
            if f.is_file():
                removed_bytes += f.stat().st_size
                f.unlink()
        except OSError:
            pass
    folder = settings.case_dir(case_id)
    if folder.exists():
        removed_bytes += sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
        shutil.rmtree(folder, ignore_errors=True)
    summary["files_removed_bytes"] = removed_bytes
    summary["folder_removed"] = not folder.exists()
    disk.forget(case_id)
    log_event(session, "case.deleted", case_id=case_id, actor=actor, details=summary)


def _require_manager(session, user: User, case_id: int) -> None:
    if not can_manage_case(session, user, case_id):
        raise HTTPException(403, "Só o responsável do caso ou um administrador pode fazer isto.")


# ---------------------------------------------------------------- membros do caso (need-to-know)
@router.get("/cases/{case_id}/members")
def list_members(case_id: int, session: SessionDep, user: CurrentUser) -> list[dict]:
    get_case_or_404(session, case_id)
    out = []
    for m in session.exec(select(CaseMember).where(CaseMember.case_id == case_id).order_by(CaseMember.id)):
        u = session.get(User, m.user_id)
        if u is not None:
            out.append({"user_id": u.id, "username": u.username, "display_name": u.display_name, "role": m.role,
                        "active": u.active, "added_by": m.added_by, "added_at": m.added_at})
    return out


@router.post("/cases/{case_id}/members", status_code=201)
def add_member(case_id: int, body: MemberAdd, session: SessionDep, user: CurrentUser) -> list[dict]:
    get_case_or_404(session, case_id)
    _require_manager(session, user, case_id)
    target = session.exec(select(User).where(User.username == body.username.strip().lower())).first()
    if target is None or not target.active:
        raise HTTPException(404, "Utilizador não encontrado ou inativo.")
    existing = membership(session, target, case_id)
    if existing is not None:
        existing.role = body.role
        session.add(existing)
    else:
        session.add(CaseMember(case_id=case_id, user_id=target.id, role=body.role, added_by=user.username))
    session.commit()
    log_event(session, "case.member_added", case_id=case_id, actor=user.username,
              details={"user": target.username, "role": body.role})
    return list_members(case_id, session, user)


@router.delete("/cases/{case_id}/members/{user_id}", status_code=204)
def remove_member(case_id: int, user_id: int, session: SessionDep, user: CurrentUser) -> None:
    get_case_or_404(session, case_id)
    _require_manager(session, user, case_id)
    m = session.exec(select(CaseMember).where(CaseMember.case_id == case_id, CaseMember.user_id == user_id)).first()
    if m is None:
        raise HTTPException(404, "Membro não encontrado.")
    owners = session.exec(select(CaseMember).where(CaseMember.case_id == case_id, CaseMember.role == "owner")).all()
    if m.role == "owner" and len(owners) <= 1:
        raise HTTPException(409, "O caso tem de ficar com pelo menos um responsável.")
    target = session.get(User, user_id)
    session.delete(m)
    session.commit()
    log_event(session, "case.member_removed", case_id=case_id, actor=user.username,
              details={"user": target.username if target else user_id})


@router.get("/cases/{case_id}/audit", response_model=list[AuditEvent])
def case_audit(case_id: int, session: SessionDep) -> list[AuditEvent]:
    get_case_or_404(session, case_id)
    return list(session.exec(select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.id)))


# ---------------------------------------------------------------- referências
@router.post("/cases/{case_id}/references", response_model=list[ReferenceImage], status_code=201)
def upload_references(
    case_id: int, files: list[UploadFile], session: SessionDep, actor: ActorDep
) -> list[ReferenceImage]:
    get_case_or_404(session, case_id)
    ref_dir = settings.case_dir(case_id) / "references"
    created: list[ReferenceImage] = []
    for upload in files:
        path, size = _store_upload(upload, ref_dir, IMAGE_EXT)
        created.append(register_reference(session, case_id, path, Path(upload.filename or "").name, size, actor))
    return created


@router.get("/cases/{case_id}/references", response_model=list[ReferenceImage])
def list_references(case_id: int, session: SessionDep) -> list[ReferenceImage]:
    q = select(ReferenceImage).where(ReferenceImage.case_id == case_id).order_by(ReferenceImage.id)
    return list(session.exec(q))


@router.get("/references/{ref_id}/image")
def reference_image(ref_id: int, session: SessionDep, face: bool = False) -> FileResponse:
    ref = session.get(ReferenceImage, ref_id)
    if ref is None:
        raise HTTPException(404, "Referência não encontrada.")
    path = ref.face_crop_path if face and ref.face_crop_path else ref.stored_path
    return FileResponse(path)


@router.patch("/references/{ref_id}", response_model=ReferenceImage)
def update_reference(ref_id: int, body: ReferenceUpdate, session: SessionDep, actor: ActorDep) -> ReferenceImage:
    """Põe a foto numa pessoa do caso (ou volta a ser solta com case_person_id=null)."""
    ref = session.get(ReferenceImage, ref_id)
    if ref is None:
        raise HTTPException(404, "Referência não encontrada.")
    person = _person_of_case(session, ref.case_id, body.case_person_id)
    if body.case_person_id != ref.case_person_id:
        previous = ref.case_person_id
        ref.case_person_id = body.case_person_id
        session.add(ref)
        session.commit()
        session.refresh(ref)
        log_event(session, "reference.person_changed", case_id=ref.case_id, actor=actor,
                  details={"reference_id": ref.id, "filename": ref.filename, "from": previous,
                           "to": body.case_person_id, "person": person.name if person else None})
    return ref


@router.post("/references/{ref_id}/remove-from-person")
def remove_from_person(ref_id: int, session: SessionDep, actor: ActorDep) -> dict:
    """Remove a foto da pessoa: é eliminada se nunca foi usada numa análise; senão fica como foto solta (a análise
    que a usou continua a referenciá-la)."""
    ref = session.get(ReferenceImage, ref_id)
    if ref is None:
        raise HTTPException(404, "Referência não encontrada.")
    used = session.exec(select(Detection).where(Detection.reference_id == ref_id)).first() is not None
    previous = ref.case_person_id
    if used:
        ref.case_person_id = None
        session.add(ref)
        session.commit()
        action = "detached"
    else:
        session.delete(ref)
        session.commit()
        action = "deleted"
    log_event(session, "reference.removed_from_person", case_id=ref.case_id, actor=actor,
              details={"reference_id": ref_id, "filename": ref.filename, "case_person_id": previous,
                       "action": action, "source_detection_id": ref.source_detection_id})
    return {"action": action}


def _person_of_case(session, case_id: int, case_person_id: int | None) -> CasePerson | None:
    if case_person_id is None:
        return None
    person = session.get(CasePerson, case_person_id)
    if person is None or person.case_id != case_id:
        raise HTTPException(422, "Pessoa inexistente neste caso.")
    return person


# ---------------------------------------------------------------- pessoas do caso
def _person_out(session, p: CasePerson) -> dict:
    refs = session.exec(select(ReferenceImage).where(ReferenceImage.case_person_id == p.id)
                        .order_by(ReferenceImage.id)).all()
    return {"id": p.id, "case_id": p.case_id, "name": p.name, "notes": p.notes, "created_by": p.created_by,
            "created_at": p.created_at, "reference_ids": [r.id for r in refs],
            "valid_references": sum(1 for r in refs if r.valid)}


def _check_name(session, case_id: int, name: str, exclude: int | None = None) -> str:
    name = " ".join(name.split())
    if not name:
        raise HTTPException(422, "Indique o nome da pessoa.")
    for other in session.exec(select(CasePerson).where(CasePerson.case_id == case_id)):
        if other.id != exclude and other.name.casefold() == name.casefold():
            raise HTTPException(409, f"Já existe a pessoa \"{other.name}\" neste caso.")
    return name


@router.get("/cases/{case_id}/persons")
def list_case_persons(case_id: int, session: SessionDep) -> list[dict]:
    get_case_or_404(session, case_id)
    return [_person_out(session, p) for p in
            session.exec(select(CasePerson).where(CasePerson.case_id == case_id).order_by(CasePerson.id))]


@router.post("/cases/{case_id}/persons", status_code=201)
def create_case_person(case_id: int, body: CasePersonCreate, session: SessionDep, actor: ActorDep) -> dict:
    get_case_or_404(session, case_id)
    p = CasePerson(case_id=case_id, name=_check_name(session, case_id, body.name), notes=body.notes.strip(),
                   created_by=actor)
    session.add(p)
    session.commit()
    session.refresh(p)
    log_event(session, "person.created", case_id=case_id, actor=actor,
              details={"case_person_id": p.id, "name": p.name, "notes": p.notes})
    return _person_out(session, p)


@router.patch("/case-persons/{case_person_id}")
def update_case_person(case_person_id: int, body: CasePersonUpdate, session: SessionDep, actor: ActorDep) -> dict:
    p = session.get(CasePerson, case_person_id)
    if p is None:
        raise HTTPException(404, "Pessoa não encontrada.")
    changes = {}
    if body.name is not None:
        name = _check_name(session, p.case_id, body.name, exclude=p.id)
        if name != p.name:
            changes["name"] = [p.name, name]
            p.name = name
    if body.notes is not None and body.notes.strip() != p.notes:
        changes["notes"] = [p.notes, body.notes.strip()]
        p.notes = body.notes.strip()
    if changes:
        session.add(p)
        session.commit()
        log_event(session, "person.updated", case_id=p.case_id, actor=actor,
                  details={"case_person_id": p.id, "changes": changes})
    return _person_out(session, p)


@router.delete("/case-persons/{case_person_id}", status_code=204)
def delete_case_person(case_person_id: int, session: SessionDep, actor: ActorDep) -> None:
    """Elimina a pessoa; as suas fotos não são apagadas, passam a fotos soltas."""
    p = session.get(CasePerson, case_person_id)
    if p is None:
        raise HTTPException(404, "Pessoa não encontrada.")
    refs = session.exec(select(ReferenceImage).where(ReferenceImage.case_person_id == p.id)).all()
    for r in refs:
        r.case_person_id = None
        session.add(r)
    session.delete(p)
    session.commit()
    log_event(session, "person.deleted", case_id=p.case_id, actor=actor,
              details={"case_person_id": case_person_id, "name": p.name, "references_released": [r.id for r in refs]})


@router.delete("/references/{ref_id}", status_code=204)
def delete_reference(ref_id: int, session: SessionDep, actor: ActorDep) -> None:
    ref = session.get(ReferenceImage, ref_id)
    if ref is None:
        raise HTTPException(404, "Referência não encontrada.")
    if session.exec(select(Detection).where(Detection.reference_id == ref_id)).first():
        raise HTTPException(409, "A referência já foi usada numa análise e não pode ser removida.")
    session.delete(ref)
    session.commit()
    log_event(session, "reference.deleted", case_id=ref.case_id, actor=actor,
              details={"reference_id": ref_id, "filename": ref.filename})


# ---------------------------------------------------------------- vídeos
@router.post("/cases/{case_id}/videos", response_model=list[Video], status_code=201)
def upload_videos(case_id: int, files: list[UploadFile], session: SessionDep, actor: ActorDep,
                  location_id: int | None = None) -> list[Video]:
    get_case_or_404(session, case_id)
    location_of_case(session, case_id, location_id, actor)  # valida antes de gravar os ficheiros
    vid_dir = settings.case_dir(case_id) / "videos"
    created: list[Video] = []
    for upload in files:
        path, size = _store_upload(upload, vid_dir, VIDEO_EXT)
        created.append(register_video(session, case_id, path, Path(upload.filename or "").name, size, actor,
                                      location_id))
    return created


@router.patch("/videos/{video_id}", response_model=Video)
def update_video(video_id: int, body: VideoUpdate, session: SessionDep, actor: ActorDep) -> Video:
    """Move o vídeo para outro local do caso e/ou muda o nome da câmara (fica na auditoria)."""
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "Vídeo não encontrado.")
    changes: dict = {}
    if body.location_id is not None and body.location_id != video.location_id:
        new = location_of_case(session, video.case_id, body.location_id)
        old = session.get(Location, video.location_id) if video.location_id else None
        changes["location"] = [{"id": video.location_id, "name": old.name if old else ""},
                               {"id": new.id, "name": new.name}]
        video.location_id = new.id
    if body.camera_name is not None:
        name = " ".join(body.camera_name.split())
        if name != video.camera_name:
            changes["camera_name"] = [video.camera_name, name]
            video.camera_name = name
    if changes:
        session.add(video)
        session.commit()
        session.refresh(video)
        log_event(session, "video.updated", case_id=video.case_id, actor=actor,
                  details={"video_id": video.id, "filename": video.filename, "changes": changes})
    return video


# ---------------------------------------------------------------- locais
def _location_out(session, loc: Location) -> dict:
    videos = session.exec(select(Video).where(Video.location_id == loc.id).order_by(Video.id)).all()
    return {"id": loc.id, "case_id": loc.case_id, "name": loc.name, "created_by": loc.created_by,
            "created_at": loc.created_at, "video_ids": [v.id for v in videos],
            "duration_s": round(sum(v.duration_s for v in videos), 3)}


def _check_location_name(session, case_id: int, name: str, exclude: int | None = None) -> str:
    name = " ".join(name.split())
    if not name:
        raise HTTPException(422, "Indique o nome do local.")
    for other in session.exec(select(Location).where(Location.case_id == case_id)):
        if other.id != exclude and other.name.casefold() == name.casefold():
            raise HTTPException(409, f"Já existe o local \"{other.name}\" neste caso.")
    return name


@router.get("/cases/{case_id}/locations")
def list_locations(case_id: int, session: SessionDep) -> list[dict]:
    get_case_or_404(session, case_id)
    return [_location_out(session, loc) for loc in
            session.exec(select(Location).where(Location.case_id == case_id).order_by(Location.id))]


@router.post("/cases/{case_id}/locations", status_code=201)
def create_location(case_id: int, body: LocationCreate, session: SessionDep, actor: ActorDep) -> dict:
    get_case_or_404(session, case_id)
    loc = Location(case_id=case_id, name=_check_location_name(session, case_id, body.name), created_by=actor)
    session.add(loc)
    session.commit()
    session.refresh(loc)
    log_event(session, "location.created", case_id=case_id, actor=actor,
              details={"location_id": loc.id, "name": loc.name})
    return _location_out(session, loc)


@router.patch("/locations/{location_id}")
def update_location(location_id: int, body: LocationCreate, session: SessionDep, actor: ActorDep) -> dict:
    loc = session.get(Location, location_id)
    if loc is None:
        raise HTTPException(404, "Local não encontrado.")
    name = _check_location_name(session, loc.case_id, body.name, exclude=loc.id)
    if name != loc.name:
        old, loc.name = loc.name, name
        session.add(loc)
        session.commit()
        log_event(session, "location.updated", case_id=loc.case_id, actor=actor,
                  details={"location_id": loc.id, "changes": {"name": [old, name]}})
    return _location_out(session, loc)


@router.delete("/locations/{location_id}", status_code=204)
def delete_location(location_id: int, session: SessionDep, actor: ActorDep) -> None:
    """Elimina um local vazio (os vídeos nunca são apagados por esta via)."""
    loc = session.get(Location, location_id)
    if loc is None:
        raise HTTPException(404, "Local não encontrado.")
    if session.exec(select(Video).where(Video.location_id == loc.id)).first():
        raise HTTPException(409, "O local tem vídeos. Mova-os para outro local ou elimine-os primeiro.")
    if session.exec(select(Upload).where(Upload.location_id == loc.id, Upload.completed_at == None)).first():  # noqa: E711
        raise HTTPException(409, "Há vídeos a ser importados para este local.")
    session.delete(loc)
    session.commit()
    log_event(session, "location.deleted", case_id=loc.case_id, actor=actor,
              details={"location_id": location_id, "name": loc.name})


@router.get("/cases/{case_id}/videos", response_model=list[Video])
def list_videos(case_id: int, session: SessionDep) -> list[Video]:
    return list(session.exec(select(Video).where(Video.case_id == case_id).order_by(Video.id)))


@router.get("/videos/{video_id}/stream")
def stream_video(video_id: int, session: SessionDep) -> FileResponse:
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "Vídeo não encontrado.")
    if video.playback_status == READY and video.playback_path and Path(video.playback_path).exists():
        media_type = FORMATS.get(video.playback_format, "video/mp4")
        return FileResponse(video.playback_path, media_type=media_type, content_disposition_type="inline",
                            headers=_NO_CACHE)
    return FileResponse(video.stored_path, filename=video.filename, content_disposition_type="inline",
                        headers=_NO_CACHE)


# O mesmo URL serve o original ou a cópia de reprodução, que pode mudar: o browser tem de revalidar.
_NO_CACHE = {"Cache-Control": "no-cache"}


@router.delete("/videos/{video_id}", status_code=204)
def delete_video(video_id: int, body: VideoDelete, session: SessionDep, user: CurrentUser) -> None:
    """Elimina um vídeo importado (original e cópia de reprodução).

    Para não deixar resultados sem o vídeo de origem, um vídeo usado em análises só pode ser eliminado depois de
    eliminadas essas análises. A auditoria é append-only: é acrescentado um "video.deleted" com o resumo e o motivo.
    """
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "Vídeo não encontrado.")
    _require_manager(session, user, video.case_id)
    if body.confirm_video_id != video_id:
        raise HTTPException(422, "Confirmação inválida: indique o número do vídeo a eliminar.")
    used = [j.id for j in session.exec(select(Job).where(Job.case_id == video.case_id).order_by(Job.id))
            if video_id in (j.video_ids or [])]
    if used:
        raise HTTPException(409, "Este vídeo é usado nas análises " + ", ".join(f"#{i}" for i in used)
                            + ". Elimine-as primeiro (os resultados dependem do vídeo original).")
    cancel_copies([video_id])
    files = [Path(p) for p in (video.stored_path, video.playback_path) if p]
    summary = {"video_id": video_id, "reason": body.reason.strip(),
               **video.model_dump(mode="json", exclude={"stored_path", "playback_path", "time_calibration"})}
    case_id = video.case_id
    session.delete(video)
    session.commit()
    removed = 0
    for f in files:
        try:
            if f.is_file():
                removed += f.stat().st_size
                f.unlink()
        except OSError:
            pass
    summary["files_removed_bytes"] = removed
    disk.forget(case_id)
    log_event(session, "video.deleted", case_id=case_id, actor=user.username, details=summary)


@router.post("/videos/{video_id}/playback", response_model=Video)
def create_playback_copy(video_id: int, session: SessionDep, actor: ActorDep, format: str = "mp4") -> Video:
    """(Re)cria a cópia de visualização, ex.: quando o browser não reproduz o original ou a conversão falhou.

    format=mp4 (H.264) ou webm (VP9, para browsers sem H.264).
    """
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "Vídeo não encontrado.")
    if format not in FORMATS:
        raise HTTPException(422, f"Formato desconhecido: {format} (use mp4 ou webm).")
    request_playback_copy(session, video, force=True, actor=actor, fmt=format)
    session.refresh(video)
    return video


@router.post("/cases/{case_id}/playback", response_model=list[Video])
def create_case_playback_copies(case_id: int, session: SessionDep, actor: ActorDep, format: str = "mp4") -> list[Video]:
    """Cria a cópia de reprodução de todos os vídeos do caso (ex.: o browser não reproduziu um deles)."""
    get_case_or_404(session, case_id)
    if format not in FORMATS:
        raise HTTPException(422, f"Formato desconhecido: {format} (use mp4 ou webm).")
    videos = request_case_copies(session, case_id, fmt=format, actor=actor)
    for v in videos:
        session.refresh(v)
    return videos


@router.get("/videos/{video_id}/probe")
def probe_video(video_id: int, session: SessionDep) -> dict:
    """Contentor e codec reais do ficheiro original (ffprobe), para diagnosticar problemas de reprodução."""
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "Vídeo não encontrado.")
    return probe_stream(video.stored_path) or {}
