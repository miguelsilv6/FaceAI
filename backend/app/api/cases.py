"""Casos, imagens de referência, vídeos e auditoria."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import select

from ..config import settings
from ..models import AuditEvent, Case, Detection, ReferenceImage, Video
from ..schemas import CaseCreate
from ..services.audit import log_event
from ..services.face_engine import get_engine
from ..services.references import validate_reference
from ..services.transcode import schedule_playback_copy
from ..services.video_processor import probe
from .deps import ActorDep, SessionDep, get_case_or_404

router = APIRouter(prefix="/api", tags=["casos"])

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


def register_video(session, case_id: int, path: Path, filename: str, size: int, actor: str) -> Video:
    """Regista um vídeo já gravado em disco (metadados via OpenCV) e pede a cópia de reprodução, se útil."""
    try:
        info = probe(path)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, f"{filename}: {exc}") from exc
    video = Video(case_id=case_id, filename=filename, stored_path=str(path), size_bytes=size,
                  duration_s=round(info.duration_s, 3), fps=round(info.fps, 3), frame_count=info.frame_count,
                  width=info.width, height=info.height, codec=info.codec)
    session.add(video)
    session.commit()
    session.refresh(video)
    log_event(session, "video.uploaded", case_id=case_id, actor=actor,
              details=video.model_dump(mode="json", exclude={"stored_path", "playback_path"}))
    if schedule_playback_copy(video):
        log_event(session, "video.playback_copy_requested", case_id=case_id, actor="sistema",
                  details={"video_id": video.id,
                           "note": "cópia H.264 apenas para visualização; o original é o analisado"})
    return video


# ---------------------------------------------------------------- casos
@router.post("/cases", response_model=Case, status_code=201)
def create_case(body: CaseCreate, session: SessionDep, actor: ActorDep) -> Case:
    case = Case(name=body.name.strip(), description=body.description, analyst=body.analyst or actor)
    session.add(case)
    session.commit()
    session.refresh(case)
    settings.case_dir(case.id).mkdir(parents=True, exist_ok=True)
    log_event(session, "case.created", case_id=case.id, actor=actor or case.analyst,
              details=body.model_dump())
    return case


@router.get("/cases", response_model=list[Case])
def list_cases(session: SessionDep) -> list[Case]:
    return list(session.exec(select(Case).order_by(Case.id.desc())))


@router.get("/cases/{case_id}", response_model=Case)
def get_case(case_id: int, session: SessionDep) -> Case:
    return get_case_or_404(session, case_id)


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
def upload_videos(case_id: int, files: list[UploadFile], session: SessionDep, actor: ActorDep) -> list[Video]:
    get_case_or_404(session, case_id)
    vid_dir = settings.case_dir(case_id) / "videos"
    created: list[Video] = []
    for upload in files:
        path, size = _store_upload(upload, vid_dir, VIDEO_EXT)
        created.append(register_video(session, case_id, path, Path(upload.filename or "").name, size, actor))
    return created


@router.get("/cases/{case_id}/videos", response_model=list[Video])
def list_videos(case_id: int, session: SessionDep) -> list[Video]:
    return list(session.exec(select(Video).where(Video.case_id == case_id).order_by(Video.id)))


@router.get("/videos/{video_id}/stream")
def stream_video(video_id: int, session: SessionDep) -> FileResponse:
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "Vídeo não encontrado.")
    path = video.playback_path if video.playback_path and Path(video.playback_path).exists() else video.stored_path
    return FileResponse(path, filename=video.filename, content_disposition_type="inline")
