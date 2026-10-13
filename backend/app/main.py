"""Ponto de entrada FastAPI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import cases, clock, jobs, system, uploads
from .api.uploads import cleanup_stale_uploads
from .config import settings
from .db import init_db
from .services.face_engine import preload_models
from .services.jobs import recover_interrupted_jobs, runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# O WeasyPrint/fontTools registam centenas de linhas INFO por cada PDF gerado.
for _noisy in ("weasyprint", "fontTools"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_dirs()
    init_db()
    recover_interrupted_jobs()
    cleanup_stale_uploads()
    preload_models()
    yield
    runner.shutdown()


app = FastAPI(title="FaceAI", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
app.include_router(system.router)
app.include_router(cases.router)
app.include_router(jobs.router)
app.include_router(uploads.router)
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
