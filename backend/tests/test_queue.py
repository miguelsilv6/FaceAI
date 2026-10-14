"""Fila de análises: prioridade, retenção, persistência ao reiniciar e limites de CPU."""

from __future__ import annotations

import time

from sqlmodel import Session

from .conftest import new_client, wait_for_job
from .fakes import RED
from .test_discover import _case_with_video, _discover

PW = "girassol-trovoada-marmelo-9"


def _db():
    import app.db

    return Session(app.db.get_engine())


def _clone(job: dict, **fields) -> int:
    """Nova análise em fila com os mesmos parâmetros de uma já feita (inserida diretamente na base de dados)."""
    from app.models import Job

    with _db() as s:
        j = Job(case_id=job["case_id"], params=job["params"], video_ids=job["video_ids"],
                reference_ids=job["reference_ids"], created_by="admin.teste", **fields)
        s.add(j)
        s.commit()
        return j.id


def test_priority_order_and_positions(client, tmp_path):
    from app.services.jobs import queue_positions, runner

    case, _ = _case_with_video(client, tmp_path, [(1.0, 2.0, RED)], duration_s=3)
    base = _discover(client, case["id"])
    low = _clone(base, priority=-1, held=True)
    normal = _clone(base, held=True)
    high = _clone(base, priority=1, held=True)
    with _db() as s:
        assert queue_positions(s) == {}  # retidas não contam
    from app.models import Job

    with _db() as s:  # liberta as três de uma vez (numa só transação), para o worker ver a fila completa
        for jid in (low, normal, high):
            j = s.get(Job, jid)
            j.held = False
            s.add(j)
        s.commit()
    with _db() as s:
        assert queue_positions(s) == {high: 1, normal: 2, low: 3}
        assert runner._next_job_id() == high
    runner.wake()
    for jid in (high, normal, low):
        assert wait_for_job(client, jid)["status"] == "completed"
    started = {jid: client.get(f"/api/jobs/{jid}").json()["started_at"] for jid in (low, normal, high)}
    assert started[high] <= started[normal] <= started[low]


def test_hold_release_and_permissions(client, tmp_path):
    case, _ = _case_with_video(client, tmp_path, [(1.0, 2.0, RED)], duration_s=3)
    base = _discover(client, case["id"])
    jid = _clone(base, held=True)
    from app.services.jobs import runner

    runner.wake()
    time.sleep(0.8)
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "queued"  # retida: não começa
    act = client.get("/api/activity").json()["jobs"]
    assert [(j["id"], j["held"], j["queue_position"]) for j in act] == [(jid, True, None)]

    # Membro do caso que não criou a análise não altera a fila; quem não tem acesso recebe 404
    member = new_client("membro.fila", PW)
    client.post(f"/api/cases/{case['id']}/members", json={"username": "membro.fila"})
    assert member.patch(f"/api/jobs/{jid}/queue", json={"held": False}).status_code == 403
    outsider = new_client("fora.fila", PW)
    assert outsider.patch(f"/api/jobs/{jid}/queue", json={"held": False}).status_code == 404
    assert client.patch(f"/api/jobs/{jid}/queue", json={"priority": 5}).status_code == 422

    r = client.patch(f"/api/jobs/{jid}/queue", json={"held": False, "priority": 1})
    assert r.status_code == 200 and r.json()["priority"] == 1
    assert wait_for_job(client, jid)["status"] == "completed"
    assert client.patch(f"/api/jobs/{jid}/queue", json={"held": True}).status_code == 409
    events = [e for e in client.get(f"/api/cases/{case['id']}/audit").json() if e["action"] == "job.queue_changed"]
    assert events[-1]["details"]["changes"] == {"priority": [0, 1], "held": [True, False]}


def test_cancel_queued_job_immediately(client, tmp_path):
    case, _ = _case_with_video(client, tmp_path, [(1.0, 2.0, RED)], duration_s=3)
    jid = _clone(_discover(client, case["id"]), held=True)
    assert client.post(f"/api/jobs/{jid}/cancel").json()["status"] == "cancelled"


def test_restart_keeps_queued_and_fails_running(client, tmp_path):
    from app.models import Job, JobStatus
    from app.services.jobs import recover_interrupted_jobs

    case, _ = _case_with_video(client, tmp_path, [(1.0, 2.0, RED)], duration_s=3)
    base = _discover(client, case["id"])
    queued = _clone(base, held=True)
    running = _clone(base, status=JobStatus.running)
    recover_interrupted_jobs()
    with _db() as s:
        assert s.get(Job, queued).status == JobStatus.queued
        assert s.get(Job, running).status == JobStatus.failed


def test_cpu_threads_applied_to_onnx(monkeypatch, tmp_path):
    import dataclasses

    import app.services.engine_onnx as eo

    captured = {}

    class FakeOrt:
        class SessionOptions:
            pass

        @staticmethod
        def InferenceSession(path, sess_options, providers):  # noqa: N802
            captured["opts"] = sess_options

        @staticmethod
        def get_available_providers():
            return ["CPUExecutionProvider"]

    import sys

    monkeypatch.setitem(sys.modules, "onnxruntime", FakeOrt)
    monkeypatch.setattr(eo, "settings", dataclasses.replace(eo.settings, cpu_threads=3))
    eo._session(tmp_path / "x.onnx")
    assert captured["opts"].intra_op_num_threads == 3 and captured["opts"].inter_op_num_threads == 1
