from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient

from .fakes import FakeEngine


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FACEAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FACEAI_FRONTEND_DIST", str(tmp_path / "no-frontend"))
    # Recarrega a configuração e a base de dados para usar a pasta temporária.
    import app.config
    import app.db

    importlib.reload(app.config)
    app.db.settings = app.config.settings
    app.db._engine = None
    from app.services import audit, face_engine, jobs, reports, transcode

    monkeypatch.setattr(transcode, "ffmpeg_available", lambda: False)

    for mod in (audit, jobs, reports):
        mod.settings = app.config.settings
    import app.api.cases as cases_api
    import app.main

    cases_api.settings = app.config.settings
    app.main.settings = app.config.settings
    face_engine.set_engine(FakeEngine())
    with TestClient(app.main.app, headers={"X-Analyst": "Analista%20Teste"}) as c:
        yield c
    face_engine.set_engine(None)
    app.db._engine = None


def wait_for_job(client, job_id: int, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id} não terminou")
