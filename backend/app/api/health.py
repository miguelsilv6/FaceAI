"""Estado de saúde para o Docker/monitorização (sem autenticação: não devolve dados dos processos nem caminhos).

200 = a funcionar (status "ok", ou "degraded" com avisos que não impedem o serviço);
503 = com problemas (base de dados inacessível, pasta de dados sem escrita ou disco cheio).
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .. import __version__
from ..config import settings
from ..db import get_engine
from ..services import disk
from ..services.jobs import runner

router = APIRouter(prefix="/api", tags=["sistema"])


def _check_db() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _check_writable() -> bool:
    probe = settings.data_dir / f".health-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except OSError:
        return False


@router.get("/health", include_in_schema=False)
def health() -> JSONResponse:
    checks: dict[str, object] = {"database": _check_db(), "data_dir_writable": _check_writable()}
    try:
        checks["disk"] = disk.disk_status()["level"]
    except OSError:
        checks["disk"] = "error"
    if not runner.enabled:
        checks["job_runner"] = "standby"  # outro processo executa as análises
    else:
        thread = runner._thread
        checks["job_runner"] = "ok" if thread is not None and thread.is_alive() else "stopped"
    healthy = checks["database"] and checks["data_dir_writable"] and checks["disk"] not in ("critical", "error")
    degraded = checks["disk"] == "warning" or checks["job_runner"] == "stopped"
    status = "error" if not healthy else "degraded" if degraded else "ok"
    return JSONResponse({"status": status, "version": __version__, "pid": os.getpid(), "checks": checks},
                        status_code=200 if healthy else 503, headers={"Cache-Control": "no-store"})
