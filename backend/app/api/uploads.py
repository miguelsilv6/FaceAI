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
import hashlib
import logging
import threading
import time
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
from .cases import (
    DUPLICATE_HEADER,
    IMAGE_EXT,
    VIDEO_EXT,
    duplicate_error,
    find_duplicate,
    location_of_case,
    register_reference,
    register_video,
)
from .deps import ActorDep, CurrentUser, SessionDep, authorize_objects, can_manage_case, get_case_or_404

# Todas as rotas exigem sessão; os objetos do caminho têm de pertencer a um caso acessível (ver deps).
router = APIRouter(prefix="/api", tags=["uploads"], dependencies=[Depends(authorize_objects)])

MAX_CHUNK_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_BYTES = 50 * 1024 * 1024
STALE_AFTER = timedelta(days=7)

# Um bloco de cada vez por upload: um reenvio do cliente enquanto o pedido anterior ainda escreve não pode
# intercalar bytes no mesmo ficheiro.
_locks: dict[str, asyncio.Lock] = {}


# A conclusão (mover o ficheiro e registar o vídeo) também é exclusiva: um "complete" repetido pelo cliente (resposta
# perdida, timeout do proxy) espera pelo primeiro e devolve o mesmo resultado.
_complete_locks: dict[str, threading.Lock] = {}
_complete_guard = threading.Lock()
# SHA-256 calculado à medida que os blocos chegam (offset já processado, estado do hash): a conclusão não tem de
# reler o ficheiro todo. Perde-se num reinício do servidor; nesse caso o hash é calculado na conclusão.
_hashers: dict[str, tuple[int, object]] = {}

log = logging.getLogger(__name__)


def _lock(upload_id: str) -> asyncio.Lock:
    return _locks.setdefault(upload_id, asyncio.Lock())


def _complete_lock(upload_id: str) -> threading.Lock:
    with _complete_guard:
        return _complete_locks.setdefault(upload_id, threading.Lock())


def _forget(upload_id: str) -> None:
    _locks.pop(upload_id, None)
    _hashers.pop(upload_id, None)
    with _complete_guard:
        _complete_locks.pop(upload_id, None)


def _feed_hash(upload_id: str, offset: int, data: bytes) -> None:
    state = _hashers.get(upload_id)
    if state is None and offset == 0:
        state = (0, hashlib.sha256())
    if state is None or state[0] != offset:
        _hashers.pop(upload_id, None)  # sequência perdida (ex.: reinício): calcula-se na conclusão
        return
    h = state[1]
    h.update(data)
    _hashers[upload_id] = (offset + len(data), h)


def _streamed_sha256(upload_id: str, size: int) -> str | None:
    state = _hashers.get(upload_id)
    return state[1].hexdigest() if state is not None and state[0] == size else None


def cleanup_stale_uploads() -> int:
    """Apaga uploads por concluir com mais de 7 dias (ficheiros .part e registos)."""
    cutoff = utcnow() - STALE_AFTER
    removed = 0
    with Session(get_engine()) as s:
        for u in s.exec(select(Upload).where(Upload.completed_at == None)):  # noqa: E711
            created = u.created_at if u.created_at.tzinfo else u.created_at.replace(tzinfo=cutoff.tzinfo)
            if created < cutoff:
                lock = _locks.get(u.id)
                if lock is not None and lock.locked():
                    continue  # está a receber um bloco agora: não é abandonado
                Path(u.temp_path).unlink(missing_ok=True)
                _forget(u.id)
                s.delete(u)
                removed += 1
        s.commit()
        # Ficheiros .part sem registo (ex.: registo apagado com o processo enquanto o envio decorria), com mais
        # de um dia: ninguém os pode retomar.
        known = {Path(p).name for p in s.exec(select(Upload.temp_path))}
    tmp_dir = settings.data_dir / "uploads"
    if tmp_dir.is_dir():
        old = time.time() - 86400
        for part in tmp_dir.glob("*.part"):
            try:
                if part.name not in known and part.stat().st_mtime < old:
                    part.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


def start_periodic_cleanup(interval_s: float = 6 * 3600) -> None:
    """Repete a limpeza dos uploads abandonados a cada 6 h (não só no arranque)."""
    def loop() -> None:
        while True:
            time.sleep(interval_s)
            try:
                cleanup_stale_uploads()
            except Exception:  # pragma: no cover
                log.exception("Limpeza periódica dos uploads falhou")

    threading.Thread(target=loop, name="faceai-upload-cleanup", daemon=True).start()


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
    location_id = None
    if body.kind == "video" and body.location_id is not None:
        location_id = location_of_case(session, case_id, body.location_id).id
    quick = body.quick_hash if body.kind == "video" else None
    if quick:  # pré-verificação de duplicados (o SHA-256 completo confirma na conclusão)
        existing = find_duplicate(session, case_id, quick=quick, size=body.size_bytes)
        if existing is not None:
            log_event(session, "video.duplicate_rejected", case_id=case_id, actor=actor,
                      details={"filename": filename, "size_bytes": body.size_bytes, "quick_hash": quick,
                               "duplicate_of": existing.id, "existing_filename": existing.filename,
                               "stage": "antes do envio"})
            raise duplicate_error(session, existing, filename)
        busy = session.exec(select(Upload).where(Upload.case_id == case_id, Upload.quick_hash == quick,
                                                 Upload.size_bytes == body.size_bytes,
                                                 Upload.completed_at == None)).first()  # noqa: E711
        if busy is not None:
            raise HTTPException(409, f"{filename}: este ficheiro já está a ser importado neste processo "
                                     f"({busy.filename}). Para retomar, largue o mesmo ficheiro no mesmo local.",
                                headers={DUPLICATE_HEADER: "upload"})
    disk.check_space(body.size_bytes)
    tmp_dir = settings.data_dir / "uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    tmp = tmp_dir / f"{upload_id}.part"
    tmp.touch()
    u = Upload(id=upload_id, case_id=case_id, kind=body.kind, location_id=location_id, quick_hash=quick or "",
               filename=filename,
               size_bytes=body.size_bytes,
               temp_path=str(tmp), created_by=actor)
    session.add(u)
    session.commit()
    log_event(session, "upload.started", case_id=case_id, actor=actor,
              details={"upload_id": upload_id, "kind": body.kind, "filename": filename,
                       "size_bytes": body.size_bytes, "location_id": location_id})
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
    _feed_hash(upload_id, offset, data)
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
    with _complete_lock(upload_id):
        return _complete(upload_id, session, actor)


def _complete(upload_id: str, session, actor: str):
    session.expire_all()  # outro pedido pode ter concluído o upload enquanto este esperava pelo lock
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
    sha256 = _streamed_sha256(upload_id, size) if u.kind == "video" else None
    tmp.replace(dest)
    result = None
    try:
        if u.kind == "video":
            result = register_video(session, u.case_id, dest, u.filename, size, actor or u.created_by,
                                    u.location_id, sha256=sha256)
        else:
            result = register_reference(session, u.case_id, dest, u.filename, size, actor or u.created_by)
        u.completed_at = utcnow()
        u.result_id = result.id
        session.add(u)
        session.commit()
    except HTTPException:
        # Ficheiro inválido (ex.: vídeo que o OpenCV não abre): o erro é definitivo, o upload é descartado.
        session.rollback()
        dest.unlink(missing_ok=True)
        u = session.get(Upload, upload_id)
        if u is not None:
            session.delete(u)
            session.commit()
        _forget(upload_id)
        raise
    except Exception:
        # Erro transitório (base de dados bloqueada, disco): o ficheiro volta para o upload, que pode ser
        # concluído outra vez sem reenviar nada.
        session.rollback()
        if result is None:
            if dest.exists():
                dest.replace(tmp)
        else:  # o vídeo ficou registado: falta só marcar o upload como concluído
            _mark_completed(upload_id, result.id)
        log.exception("Conclusão do upload %s falhou", upload_id)
        raise HTTPException(503, "Não foi possível concluir a importação agora; tente concluir de novo.") from None
    _forget(upload_id)
    return result


def _mark_completed(upload_id: str, result_id: int) -> None:
    try:
        with Session(get_engine()) as s:
            u = s.get(Upload, upload_id)
            if u is not None and u.completed_at is None:
                u.completed_at, u.result_id = utcnow(), result_id
                s.add(u)
                s.commit()
    except Exception:  # pragma: no cover
        log.exception("Upload %s: não foi possível marcar como concluído", upload_id)


@router.delete("/uploads/{upload_id}", status_code=204)
def cancel_upload(upload_id: str, session: SessionDep, actor: ActorDep, user: CurrentUser) -> None:
    u = _get_open(session, upload_id)
    if u.created_by and u.created_by != actor and not can_manage_case(session, user, u.case_id):
        raise HTTPException(403, "Só quem iniciou o envio, o responsável do processo ou um administrador o cancela.")
    Path(u.temp_path).unlink(missing_ok=True)
    _forget(upload_id)
    session.delete(u)
    session.commit()
    log_event(session, "upload.cancelled", case_id=u.case_id, actor=actor,
              details={"upload_id": upload_id, "filename": u.filename, "received_bytes": u.received_bytes})
