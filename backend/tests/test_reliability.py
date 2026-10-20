"""Fiabilidade forense: interrupções, repetição, ids, cadeia da auditoria, integridade, uploads e backup."""

from __future__ import annotations

import sqlite3
import time

from sqlmodel import Session, select

from .conftest import wait_for_job
from .fakes import RED, make_image, make_video


def _case_with_video(client, tmp_path, *, duration_s=4, name="cctv.mp4"):
    cid = client.post("/api/cases", json={"name": "Fiabilidade"}).json()["id"]
    with make_image(tmp_path / "ref.jpg", RED).open("rb") as fh:
        client.post(f"/api/cases/{cid}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    path = make_video(tmp_path / name, [(1, duration_s - 1, RED)], duration_s=duration_s, fps=10)
    with path.open("rb") as fh:
        video = client.post(f"/api/cases/{cid}/videos", files=[("files", (name, fh, "video/mp4"))]).json()[0]
    return cid, video


def _start(client, cid, **params):
    body = {"params": {"sample_fps": 10, "threshold": 0.05, "motion_filter": False, "tracking": True, **params}}
    r = client.post(f"/api/cases/{cid}/jobs", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _actions(client, cid):
    return [e for e in client.get(f"/api/cases/{cid}/audit").json()]


def test_shutdown_requeues_instead_of_cancelling(client, tmp_path):
    from app.services.jobs import runner

    cid, _ = _case_with_video(client, tmp_path, duration_s=60)
    jid = _start(client, cid)
    deadline = time.time() + 30
    while client.get(f"/api/jobs/{jid}").json()["frames_sampled"] < 5:
        assert time.time() < deadline
        time.sleep(0.05)
    runner.shutdown()
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "queued" and job["partial"] and "desligar" in job["message"]
    ev = [e for e in _actions(client, cid) if e["action"] == "job.interrupted"]
    assert ev and ev[-1]["actor"] == "sistema" and ev[-1]["details"]["requeued"] is True
    assert not any(e["action"] == "job.cancelled" for e in _actions(client, cid))
    # no "arranque" seguinte a análise recomeça do início, sem resultados duplicados
    runner.start()
    job = wait_for_job(client, jid, timeout=60)
    assert job["status"] == "completed" and not job["partial"]
    assert job["frames_sampled"] == 600
    assert any(e["action"] == "job.results_reset" for e in _actions(client, cid))
    dets = client.get(f"/api/jobs/{jid}/detections").json()
    assert len({(d["video_id"], d["frame_index"]) for d in dets}) == len(dets)


def test_crash_marks_partial_and_retry(client, tmp_path):
    import app.db
    from app.models import Job, JobStatus
    from app.services.jobs import recover_interrupted_jobs

    cid, _ = _case_with_video(client, tmp_path)
    jid = _start(client, cid)
    first = wait_for_job(client, jid)
    assert first["status"] == "completed"
    with Session(app.db.get_engine()) as s:  # simula uma queda do servidor a meio
        job = s.get(Job, jid)
        job.status = JobStatus.running
        s.add(job)
        s.commit()
    recover_interrupted_jobs()
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "failed" and job["partial"]
    assert client.post(f"/api/jobs/{jid}/retry").status_code == 200
    job = wait_for_job(client, jid)
    assert job["status"] == "completed" and not job["partial"] and job["matches"] == first["matches"]
    assert client.post(f"/api/jobs/{jid}/retry").status_code == 409  # concluída: não se repete
    assert "job.retried" in [e["action"] for e in _actions(client, cid)]


def test_ids_never_reused_after_delete(client):
    a = client.post("/api/cases", json={"name": "A"}).json()["id"]
    b = client.post("/api/cases", json={"name": "B"}).json()["id"]
    r = client.request("DELETE", f"/api/cases/{b}", json={"confirm_case_id": b, "reason": "teste"})
    assert r.status_code == 204
    c = client.post("/api/cases", json={"name": "C"}).json()["id"]
    assert a < b < c


def test_audit_hash_chain_detects_tampering(client):
    import app.db
    from app.models import AuditEvent

    cid = client.post("/api/cases", json={"name": "Auditoria"}).json()["id"]
    client.patch(f"/api/cases/{cid}", json={"description": "x"})
    r = client.get(f"/api/cases/{cid}/audit/verify").json()
    assert r["ok"] and r["events"] > 0 and r["unsealed"] == 0 and r["case_events"] >= 1
    with Session(app.db.get_engine()) as s:
        ev = s.exec(select(AuditEvent).where(AuditEvent.case_id == cid).order_by(AuditEvent.id)).first()
        target = ev.id
    with sqlite3.connect(app.db.settings.data_dir / "faceai.db") as db:  # alteração fora da aplicação
        db.execute("UPDATE auditevent SET actor = 'outro' WHERE id = ?", (target,))
    with Session(app.db.get_engine()) as s:
        from app.services.audit import verify_chain

        broken = verify_chain(s)
    assert not broken["ok"] and broken["broken_at"] == target


def test_audit_detects_events_removed_from_the_end(client):
    import app.db
    from app.services.audit import verify_chain

    client.post("/api/cases", json={"name": "Fim"})
    with sqlite3.connect(app.db.settings.data_dir / "faceai.db") as db:
        db.execute("DELETE FROM auditevent WHERE id = (SELECT MAX(id) FROM auditevent)")
    with Session(app.db.get_engine()) as s:
        r = verify_chain(s)
    assert not r["ok"] and "audit.jsonl" in r["reason"]


def test_evidence_verification_detects_altered_video(client, tmp_path):
    import app.db
    from app.models import Video

    cid, video = _case_with_video(client, tmp_path)
    jid = _start(client, cid)
    wait_for_job(client, jid)
    r = client.post(f"/api/cases/{cid}/evidence/verify")
    assert r.status_code == 200
    deadline = time.time() + 20
    while client.get(f"/api/cases/{cid}/evidence/verify").json()["running"]:
        assert time.time() < deadline
        time.sleep(0.05)
    state = client.get(f"/api/cases/{cid}/evidence/verify").json()
    assert state["ok"] == 1 and not state["failed"]
    with Session(app.db.get_engine()) as s:
        path = s.get(Video, video["id"]).stored_path
    with open(path, "r+b") as fh:  # um byte alterado no original
        fh.seek(100)
        b = fh.read(1)
        fh.seek(100)
        fh.write(bytes([b[0] ^ 0xFF]))
    with Session(app.db.get_engine()) as s:  # força a verificação na hora ao gerar o relatório
        v = s.get(Video, video["id"])
        v.verified_at = None
        s.add(v)
        s.commit()
    html = client.get(f"/api/jobs/{jid}/export/html").text
    assert "NÃO CORRESPONDE" in html and "Integridade:" in html
    assert "cadeia de hashes íntegra" in html
    assert client.get(f"/api/cases/{cid}/videos").json()[0]["verified_ok"] is False
    assert any(e["action"] == "evidence.verified" and e["details"]["failed"] for e in _actions(client, cid))


def test_upload_complete_uses_streamed_hash_and_survives_errors(client, tmp_path, monkeypatch):
    import hashlib

    import app.api.uploads as uploads

    cid = client.post("/api/cases", json={"name": "Upload"}).json()["id"]
    data = make_video(tmp_path / "u.mp4", [(1, 2, RED)], duration_s=3, fps=10).read_bytes()
    up = client.post(f"/api/cases/{cid}/uploads",
                     json={"kind": "video", "filename": "u.mp4", "size_bytes": len(data)}).json()
    half = len(data) // 2
    client.put(f"/api/uploads/{up['id']}?offset=0", content=data[:half])
    client.put(f"/api/uploads/{up['id']}?offset={half}", content=data[half:])
    assert uploads._streamed_sha256(up["id"], len(data)) == hashlib.sha256(data).hexdigest()
    real = uploads.register_video

    def boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(uploads, "register_video", boom)
    r = client.post(f"/api/uploads/{up['id']}/complete")
    assert r.status_code == 503
    assert client.get(f"/api/uploads/{up['id']}").json()["received_bytes"] == len(data)  # ficheiro reposto
    monkeypatch.setattr(uploads, "register_video", real)
    video = client.post(f"/api/uploads/{up['id']}/complete").json()
    assert video["sha256"] == hashlib.sha256(data).hexdigest()


def test_backup_snapshot(client, tmp_path):
    from app.services.backup import backup

    client.post("/api/cases", json={"name": "Backup"})
    m = backup(tmp_path / "bk", include_files=True)
    with sqlite3.connect(tmp_path / "bk" / m["database"]["file"]) as db:
        assert db.execute("SELECT count(*) FROM 'case' WHERE name = 'Backup'").fetchone()[0] == 1
    assert (tmp_path / "bk" / f"manifest_{m['created_at']}.json").exists()


def test_backup_includes_secret_key(client, tmp_path):
    import stat

    from app.config import settings
    from app.services import auth
    from app.services.backup import backup

    auth.encrypt("x")  # cria data/secret.key se ainda não existir
    m = backup(tmp_path / "bk2")
    key = tmp_path / "bk2" / "secret.key"
    assert m["secret_key"]["file"] == "secret.key"
    assert key.read_bytes() == (settings.data_dir / "secret.key").read_bytes()
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
