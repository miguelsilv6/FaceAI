"""Ponto de entrada FastAPI."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .api import activity, cases, clock, imports, jobs, system, uploads, users
from .api import auth as auth_api
from .api.deps import UNSAFE_METHODS, client_ip, is_https
from .api.uploads import cleanup_stale_uploads
from .config import settings
from .db import get_engine, init_db
from .services import auth, disk
from .services.audit import request_ip
from .services.clock_auto import resume_pending_auto_clock
from .services.face_engine import preload_models
from .services.hashing import start_backfill
from .services.jobs import recover_interrupted_jobs, runner
from .services.locations import migrate_locations
from .services.references import migrate_person_labels
from .services.transcode import resume_pending_copies

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# O WeasyPrint/fontTools registam centenas de linhas INFO por cada PDF gerado.
for _noisy in ("weasyprint", "fontTools"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_dirs()
    init_db()
    with Session(get_engine()) as s:
        migrate_person_labels(s)
        migrate_locations(s)
        disk.report_orphan_folders(s)
    recover_interrupted_jobs()
    cleanup_stale_uploads()
    resume_pending_copies()
    resume_pending_auto_clock()
    start_backfill()
    preload_models()
    if settings.cpu_threads > 0:
        cv2.setNumThreads(settings.cpu_threads)
    runner.start()  # retoma as análises que ficaram em fila
    yield
    runner.shutdown(timeout=settings.shutdown_grace_s)


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
# Respondem mesmo antes de existir o primeiro administrador.
SETUP_EXEMPT = {"/api/auth/status"}
_setup_state = {"done": False}


class SecurityMiddleware:
    """Cabeçalhos de segurança, verificação de Origin/Referer nos pedidos que alteram dados, IP do cliente para a
    auditoria e bloqueio da API até ser criado o primeiro administrador (python -m app.cli create-admin)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        https = is_https(request)
        path = scope["path"]
        if path.startswith("/api/"):
            if scope["method"] in UNSAFE_METHODS and not self._origin_ok(request):
                await self._reject(scope, send, 403, "Pedido recusado: origem não permitida.", https)
                return
            if path not in SETUP_EXEMPT and not self._setup_done():
                await self._reject(scope, send, 503, "Configuração inicial pendente: crie o primeiro administrador "
                                                     "com 'python -m app.cli create-admin'.", https)
                return
        token = request_ip.set(client_ip(request))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + self._headers(https)
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            request_ip.reset(token)

    @staticmethod
    def _headers(https: bool) -> list[tuple[bytes, bytes]]:
        extra = [(b"strict-transport-security", b"max-age=31536000; includeSubDomains")] if https else []
        return SECURITY_HEADERS + extra

    @staticmethod
    def _origin_ok(request: Request) -> bool:
        """Sem Origin nem Referer (clientes que não são navegadores) passa: o token CSRF continua a ser exigido."""
        origin = request.headers.get("origin") or ""
        if not origin or origin == "null":
            referer = request.headers.get("referer") or ""
            if not referer:
                return origin != "null"
            parts = urlsplit(referer)
            origin = f"{parts.scheme}://{parts.netloc}"
        origin = origin.rstrip("/")
        if origin in settings.allowed_origins or origin in settings.cors_origins:
            return True
        return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()

    @staticmethod
    def _setup_done() -> bool:
        if not _setup_state["done"]:  # depois de existir um admin já não se verifica (a API não deixa ficar sem)
            with Session(get_engine()) as s:
                _setup_state["done"] = not auth.setup_required(s)
        return _setup_state["done"]

    async def _reject(self, scope: Scope, send: Send, status: int, detail: str, https: bool) -> None:
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())] + self._headers(https)})
        await send({"type": "http.response.body", "body": body})


app = FastAPI(title="FaceAI", version=__version__, lifespan=lifespan,
              docs_url="/api/docs" if settings.api_docs else None, redoc_url=None,
              openapi_url="/api/openapi.json" if settings.api_docs else None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in settings.cors_origins if o != "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Range"],
    expose_headers=["Content-Disposition"],
)
app.add_middleware(SecurityMiddleware)
app.include_router(auth_api.router)
app.include_router(users.router)
app.include_router(activity.router)
app.include_router(system.router)
app.include_router(cases.router)
app.include_router(jobs.router)
app.include_router(uploads.router)
app.include_router(imports.router)
app.include_router(clock.router)

# Em produção, o build do frontend (frontend/dist) é servido pelo próprio backend.
if settings.frontend_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=settings.frontend_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(404, "Endpoint não encontrado.")
        candidate = (settings.frontend_dist / full_path).resolve()
        if full_path and candidate.is_file() and settings.frontend_dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(settings.frontend_dist / "index.html")
