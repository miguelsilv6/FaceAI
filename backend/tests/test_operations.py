"""Operação: saúde, um só executor, cancelamento entre processos, estados presos, exportações, pasta de dados movida,
limpeza de uploads e descarga dos modelos sem rede."""

from __future__ import annotations

import io
import os
import threading
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from .conftest import wait_for_job
from .fakes import RED, make_image, make_video


def _case_video(client, tmp_path, duration_s=4):
    cid = client.post("/api/cases", json={"name": "Operação"}).json()["id"]
    path = make_video(tmp_path / "v.mp4", [(0.5, duration_s - 0.5, RED)], duration_s=duration_s, fps=10)
    with path.open("rb") as fh:
        vid = client.post(f"/api/cases/{cid}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))]).json()[0]
    with open(make_image(tmp_path / "r.jpg", RED), "rb") as fh:
        client.post(f"/api/cases/{cid}/references", files=[("files", ("r.jpg", fh, "image/jpeg"))])
    return cid, vid


def _job(client, cid, **params):
    body = {"params": {"sample_fps": 10, "threshold": 0.05, "motion_filter": False, "tracking": True, **params}}
    return client.post(f"/api/cases/{cid}/jobs", json=body).json()["id"]


def test_health_is_public_and_reports_problems(client, monkeypatch):
    import app.main
    from app.services import disk

    anon = TestClient(app.main.app)
    r = anon.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] in ("ok", "degraded")
    checks = r.json()["checks"]
    assert checks["database"] is True and checks["data_dir_writable"] is True and checks["job_runner"] == "ok"
    assert "path" not in r.text and r.headers["cache-control"] == "no-store"
    monkeypatch.setattr(disk, "disk_status", lambda: {"level": "critical"})
    r = anon.get("/api/health")
    assert r.status_code == 503 and r.json()["status"] == "error"


def test_single_runner_lock_and_atomic_claim(client, tmp_path):
    import app.db
    from app.models import Job, JobStatus
    from app.services import jobs

    assert jobs.acquire_runner_lock() is False  # o servidor (lifespan) já tem o bloqueio
    cid, _ = _case_video(client, tmp_path)
    jid = _job(client, cid)
    wait_for_job(client, jid)
    with Session(app.db.get_engine()) as s:  # outro processo já a está a correr
        job = s.get(Job, jid)
        job.status, job.frames_sampled = JobStatus.running, 123
        s.add(job)
        s.commit()
    jobs.run_job(jid, threading.Event())
    with Session(app.db.get_engine()) as s:
        job = s.get(Job, jid)
        assert job.status == JobStatus.running and job.frames_sampled == 123  # não foi reclamada


def test_cancel_request_from_another_process(client, tmp_path):
    import app.db
    from app.models import Job

    # vídeo longo (2400 frames): mesmo numa máquina rápida a análise ainda corre quando o pedido chega
    cid, _ = _case_video(client, tmp_path, duration_s=240)
    jid = _job(client, cid)
    deadline = time.time() + 30
    while client.get(f"/api/jobs/{jid}").json()["status"] != "running":
        assert time.time() < deadline
        time.sleep(0.05)
    with Session(app.db.get_engine()) as s:  # o pedido chega pela base de dados (outro worker)
        job = s.get(Job, jid)
        job.cancel_requested = True
        s.add(job)
        s.commit()
    job = wait_for_job(client, jid, timeout=120)
    assert job["status"] == "cancelled" and job["frames_sampled"] < 2400


def test_stuck_pending_states_are_resolved(client, tmp_path):
    import app.db
    from app.models import Video
    from app.services import clock_auto, transcode

    cid, vid = _case_video(client, tmp_path)
    with Session(app.db.get_engine()) as s:
        v = s.get(Video, vid["id"])
        v.playback_status, v.clock_auto_status = "pending", "pending"
        s.add(v)
        s.commit()
    transcode.resume_pending_copies()  # nos testes não há ffmpeg
    clock_auto.resume_pending_auto_clock()  # nos testes o OCR automático está desligado
    v = client.get(f"/api/cases/{cid}/videos").json()[0]
    assert v["playback_status"] == "unavailable" and "ffmpeg" in v["playback_error"]
    assert v["clock_auto_status"] == "unavailable"


def test_export_zip_from_disk_and_failure_is_audited(client, tmp_path, monkeypatch):
    import app.db
    from app.services import reports

    cid, _ = _case_video(client, tmp_path)
    jid = _job(client, cid)
    wait_for_job(client, jid)
    r = client.get(f"/api/jobs/{jid}/export/zip")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "relatorio.html" in names and any(n.startswith("crops/") for n in names)
    assert not list((app.db.settings.data_dir / "tmp").glob("*.zip"))  # o temporário é apagado depois do envio

    def boom(*a, **k):
        raise RuntimeError("WeasyPrint falhou")

    monkeypatch.setattr(reports, "render_pdf", boom)
    r = client.get(f"/api/jobs/{jid}/export/pdf")
    assert r.status_code == 500 and "WeasyPrint" in r.json()["detail"]
    events = [e for e in client.get(f"/api/cases/{cid}/audit").json() if e["action"] == "job.export_failed"]
    assert events and events[-1]["details"]["format"] == "pdf"


def test_data_dir_relocation(client, tmp_path):
    from sqlalchemy import text

    import app.db
    from app.models import Video
    from app.services.relocate import relocate_data_dir

    cid, vid = _case_video(client, tmp_path)
    jid = _job(client, cid)
    wait_for_job(client, jid)
    current = str(app.db.settings.data_dir)
    old = "/mnt/disco-antigo/faceai-data"
    with Session(app.db.get_engine()) as s:  # simula uma base de dados restaurada de outra máquina
        for table, col in (("video", "stored_path"), ("detection", "crop_path"), ("detection", "frame_path"),
                           ("referenceimage", "stored_path")):
            s.exec(text(f'UPDATE "{table}" SET "{col}" = replace("{col}", :cur, :old)'),
                   params={"cur": current, "old": old})
        s.commit()
    assert relocate_data_dir() > 0
    with Session(app.db.get_engine()) as s:
        v = s.get(Video, vid["id"])
        assert v.stored_path.startswith(current) and os.path.exists(v.stored_path)
    det = client.get(f"/api/jobs/{jid}/detections").json()[0]
    assert client.get(f"/api/detections/{det['id']}/crop").status_code == 200
    from sqlmodel import select

    from app.models import AuditEvent

    with Session(app.db.get_engine()) as s:
        assert s.exec(select(AuditEvent).where(AuditEvent.action == "system.data_dir_relocated")).first()
    assert relocate_data_dir() == 0  # idempotente


def test_orphan_upload_parts_are_cleaned(client):
    import app.db
    from app.api.uploads import cleanup_stale_uploads

    d = app.db.settings.data_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    orphan, recent = d / "orfao.part", d / "recente.part"
    orphan.write_bytes(b"x")
    recent.write_bytes(b"y")
    old = time.time() - 3 * 86400
    os.utime(orphan, (old, old))
    cleanup_stale_uploads()
    assert not orphan.exists() and recent.exists()


def test_model_download_offline_gives_clear_error(tmp_path, monkeypatch):
    import urllib.error

    from app.services import engine_onnx

    monkeypatch.setattr(engine_onnx, "models_dir", lambda: tmp_path)

    def offline(*a, **k):
        raise urllib.error.URLError("sem rede")

    monkeypatch.setattr(engine_onnx.urllib.request, "urlopen", offline)
    mf = next(iter(engine_onnx.DETECTORS.values()))
    with pytest.raises(RuntimeError, match="offline"):
        engine_onnx.ensure_model(mf)
    assert not list(tmp_path.rglob("*.onnx"))
