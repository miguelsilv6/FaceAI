"""Upload em blocos com retoma (vídeos grandes, túneis com limite por pedido como o Cloudflare).

Protocolo:
1. ``POST /api/cases/{id}/uploads`` {kind, filename, size_bytes} → {id, received_bytes, chunk_size}
2. ``PUT  /api/uploads/{id}?offset=N`` com o bloco no corpo (application/octet-stream). O ``offset`` tem de
   ser igual aos bytes já recebidos; caso contrário devolve 409 com ``received_bytes`` para o cliente retomar.
3. ``GET  /api/uploads/{id}`` → estado (para retomar após falha de rede ou recarregar a página).
4. ``POST /api/uploads/{id}/complete`` → move o ficheiro para o caso e regista o vídeo/referência.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine
from ..models import ReferenceImage, Upload, Video, utcnow
from ..schemas import UploadCreate
from ..services import disk
from ..services.audit import log_event
from .cases import IMAGE_EXT, VIDEO_EXT, register_reference, register_video
from .deps import ActorDep, SessionDep, authorize_objects, get_case_or_404

# Todas as rotas exigem sessão; os objetos do caminho têm de pertencer a um caso acessível (ver deps).
router = APIRouter(prefix="/api", tags=["uploads"], dependencies=[Depends(authorize_objects)])

MAX_CHUNK_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_BYTES = 50 * 1024 * 1024
STALE_AFTER = timedelta(days=7)

# Um bloco de cada vez por upload: um reenvio do cliente enquanto o pedido anterior ainda escreve não pode
# intercalar bytes no mesmo ficheiro.
_locks: dict[str, asyncio.Lock] = {}


def _lock(upload_id: str) -> asyncio.Lock:
    return _locks.setdefault(upload_id, asyncio.Lock())


def cleanup_stale_uploads() -> int:
    """Apaga uploads por concluir com mais de 7 dias (ficheiros .part e registos)."""
    cutoff = utcnow() - STALE_AFTER
    removed = 0
    with Session(get_engine()) as s:
        for u in s.exec(select(Upload).where(Upload.completed_at == None)):  # noqa: E711
            created = u.created_at if u.created_at.tzinfo else u.created_at.replace(tzinfo=cutoff.tzinfo)
            if created < cutoff:
                Path(u.temp_path).unlink(missing_ok=True)
                s.delete(u)
                removed += 1
        s.commit()
    return removed


def _status(u: Upload) -> dict:
    return {"id": u.id, "kind": u.kind, "filename": u.filename, "size_bytes": u.size_bytes,
            "received_bytes": u.received_bytes, "completed": u.completed_at is not None,
            "chunk_size": chunk_size()}


def chunk_size() -> int:
    """Tamanho de bloco anunciado ao cliente — nunca acima do máximo que o servidor aceita."""
    return max(1, min(settings.upload_chunk_mb * 1024 * 1024, MAX_CHUNK_BYTES))


def _get_open(session, upload_id: str) -> Upload:
    u = session.get(Upload, upload_id)
    if u is None:
        raise HTTPException(404, "Upload não encontrado.")
    if u.completed_at is not None:
        raise HTTPException(409, "Upload já concluído.")
    return u


@router.post("/cases/{case_id}/uploads", status_code=201)
def create_upload(case_id: int, body: UploadCreate, session: SessionDep, actor: ActorDep) -> dict:
    get_case_or_404(session, case_id)
    filename = Path(body.filename).name
    ext = Path(filename).suffix.lower()
    allowed = VIDEO_EXT if body.kind == "video" else IMAGE_EXT
    if ext not in allowed:
        raise HTTPException(415, f"Extensão não suportada: '{ext or filename}'.")
    if body.kind == "reference" and body.size_bytes > MAX_REFERENCE_BYTES:
        raise HTTPException(413, "Imagem de referência demasiado grande.")
    disk.check_space(body.size_bytes)
    tmp_dir = settings.data_dir / "uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    tmp = tmp_dir / f"{upload_id}.part"
    tmp.touch()
    u = Upload(id=upload_id, case_id=case_id, kind=body.kind, filename=filename, size_bytes=body.size_bytes,
               temp_path=str(tmp), created_by=actor)
    session.add(u)
    session.commit()
    log_event(session, "upload.started", case_id=case_id, actor=actor,
              details={"upload_id": upload_id, "kind": body.kind, "filename": filename,
                       "size_bytes": body.size_bytes})
    return _status(u)


@router.get("/uploads/{upload_id}")
def upload_status(upload_id: str, session: SessionDep) -> dict:
    u = session.get(Upload, upload_id)
    if u is None:
        raise HTTPException(404, "Upload não encontrado.")
    return _status(u)


@router.put("/uploads/{upload_id}")
async def upload_chunk(upload_id: str, offset: int, request: Request, session: SessionDep):
    async with _lock(upload_id):
        # O corpo é lido de forma assíncrona; a escrita em disco e a base de dados (bloqueantes) correm numa
        # thread, para não parar o event loop (e os restantes pedidos) durante uploads grandes.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_CHUNK_BYTES:
                raise HTTPException(413, "Bloco demasiado grande.")
        return await run_in_threadpool(_write_chunk, session, upload_id, offset, bytes(body))


def _write_chunk(session, upload_id: str, offset: int, data: bytes):
    u = _get_open(session, upload_id)
    tmp = Path(u.temp_path)
    # O ficheiro em disco manda (ex.: ligação cortada a meio de um bloco já parcialmente escrito).
    u.received_bytes = tmp.stat().st_size if tmp.exists() else 0
    if offset != u.received_bytes:
        session.add(u)
        session.commit()
        return JSONResponse(status_code=409, content={**_status(u), "detail": "Offset inesperado."})
    if u.received_bytes + len(data) > u.size_bytes:
        raise HTTPException(413, "O bloco excede o tamanho declarado do ficheiro.")
    with tmp.open("ab") as fh:
        fh.write(data)
    u.received_bytes += len(data)
    session.add(u)
    session.commit()
    return _status(u)


def _result(session, u: Upload):
    model = Video if u.kind == "video" else ReferenceImage
    obj = session.get(model, u.result_id) if u.result_id is not None else None
    if obj is None:
        raise HTTPException(409, "Upload já concluído, mas o registo resultante não existe.")
    return obj


@router.post("/uploads/{upload_id}/complete")
def complete_upload(upload_id: str, session: SessionDep, actor: ActorDep):
    u = session.get(Upload, upload_id)
    if u is None:
        raise HTTPException(404, "Upload não encontrado.")
    if u.completed_at is not None:
        # Idempotente: se a resposta anterior se perdeu (rede, timeout do proxy), devolve o mesmo resultado
        # em vez de obrigar a reenviar o ficheiro e criar um duplicado.
        return _result(session, u)
    tmp = Path(u.temp_path)
    size = tmp.stat().st_size if tmp.exists() else 0
    if size != u.size_bytes:
        raise HTTPException(409, f"Upload incompleto: {size} de {u.size_bytes} bytes recebidos.")
    sub = "videos" if u.kind == "video" else "references"
    dest_dir = settings.case_dir(u.case_id) / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}{Path(u.filename).suffix.lower()}"
    tmp.replace(dest)
    _locks.pop(upload_id, None)
    try:
        if u.kind == "video":
            result = register_video(session, u.case_id, dest, u.filename, size, actor or u.created_by)
        else:
            result = register_reference(session, u.case_id, dest, u.filename, size, actor or u.created_by)
    except HTTPException:
        # Ficheiro inválido (ex.: vídeo que o OpenCV não abre): o erro é definitivo, o upload é descartado.
        dest.unlink(missing_ok=True)
        session.delete(u)
        session.commit()
        raise
    u.completed_at = utcnow()
    u.result_id = result.id
    session.add(u)
    session.commit()
    return result


@router.delete("/uploads/{upload_id}", status_code=204)
def cancel_upload(upload_id: str, session: SessionDep, actor: ActorDep) -> None:
    u = _get_open(session, upload_id)
    Path(u.temp_path).unlink(missing_ok=True)
    session.delete(u)
    session.commit()
    log_event(session, "upload.cancelled", case_id=u.case_id, actor=actor,
              details={"upload_id": upload_id, "filename": u.filename, "received_bytes": u.received_bytes})
