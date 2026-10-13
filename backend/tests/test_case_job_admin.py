"""Edição do caso e eliminação de análises (com confirmação), ambas auditadas."""

from .conftest import wait_for_job
from .fakes import BLUE, RED, make_image, make_video


def _case_with_job(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Caso original", "description": "desc"}).json()
    img = make_image(tmp_path / "ref.jpg", RED)
    with img.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    video = make_video(tmp_path / "v.mp4", [(1.0, 4.0, RED), (0.0, 6.0, BLUE)], duration_s=6, fps=10)
    with video.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    r = client.post(f"/api/cases/{case['id']}/jobs",
                    json={"params": {"sample_fps": 2, "threshold": 0.05, "motion_filter": False}})
    return case, wait_for_job(client, r.json()["id"])


def test_update_case(client, tmp_path):
    case = client.post("/api/cases", json={"name": "NUIPC 1/26", "description": "a"}).json()
    r = client.patch(f"/api/cases/{case['id']}", json={"name": "  NUIPC 1/26 — Loja Centro  "})
    assert r.status_code == 200 and r.json()["name"] == "NUIPC 1/26 — Loja Centro" and r.json()["description"] == "a"
    assert client.patch(f"/api/cases/{case['id']}", json={"name": ""}).status_code == 422
    assert client.patch(f"/api/cases/{case['id']}", json={"name": "   "}).status_code == 422
    client.patch(f"/api/cases/{case['id']}", json={"name": "NUIPC 1/26 — Loja Centro"})  # sem alterações
    assert client.patch("/api/cases/999", json={"name": "x"}).status_code == 404
    events = [e for e in client.get(f"/api/cases/{case['id']}/audit").json() if e["action"] == "case.updated"]
    assert len(events) == 1
    assert events[0]["details"]["name"] == {"from": "NUIPC 1/26", "to": "NUIPC 1/26 — Loja Centro"}
    assert events[0]["actor"] == "Analista Teste"


def test_delete_job(client, tmp_path):
    from app.config import settings

    case, job = _case_with_job(client, tmp_path)
    assert job["status"] == "completed" and job["matches"] > 0
    det = client.get(f"/api/jobs/{job['id']}/detections").json()[0]
    client.patch(f"/api/detections/{det['id']}/review", json={"status": "confirmed"})
    folder = settings.case_dir(case["id"]) / "jobs" / f"job_{job['id']:05d}"
    assert (folder / "crops").is_dir()

    url = f"/api/jobs/{job['id']}"
    assert client.request("DELETE", url, json={"confirm_job_id": job["id"] + 1}).status_code == 422
    assert client.request("DELETE", url, json={}).status_code == 422
    assert client.get(url).status_code == 200  # nada foi apagado

    r = client.request("DELETE", url, json={"confirm_job_id": job["id"], "reason": "parâmetros errados"})
    assert r.status_code == 204
    assert client.get(url).status_code == 404
    assert client.get(f"/api/detections/{det['id']}/crop").status_code == 404
    assert client.get(f"/api/cases/{case['id']}/jobs").json() == []
    assert not folder.exists()
    # Vídeos e referências do caso mantêm-se; a referência deixa de estar em uso e pode ser removida.
    assert len(client.get(f"/api/cases/{case['id']}/videos").json()) == 1
    refs = client.get(f"/api/cases/{case['id']}/references").json()
    assert len(refs) == 1 and client.delete(f"/api/references/{refs[0]['id']}").status_code == 204

    audit = client.get(f"/api/cases/{case['id']}/audit").json()
    deleted = [e for e in audit if e["action"] == "job.deleted"]
    assert len(deleted) == 1
    d = deleted[0]["details"]
    assert d["reason"] == "parâmetros errados" and d["detections"] > 0 and d["reviewed_detections"] == 1
    assert d["files_removed"] and d["params"]["threshold"] == 0.05
    assert any(e["action"] == "job.completed" for e in audit)  # o histórico da análise mantém-se
    assert client.request("DELETE", url, json={"confirm_job_id": job["id"]}).status_code == 404


def test_cannot_delete_running_job(client, tmp_path, monkeypatch):
    from app.models import Job, JobStatus

    case, job = _case_with_job(client, tmp_path)
    from sqlmodel import Session

    import app.db

    with Session(app.db.get_engine()) as s:
        row = s.get(Job, job["id"])
        row.status = JobStatus.running
        s.add(row)
        s.commit()
    r = client.request("DELETE", f"/api/jobs/{job['id']}", json={"confirm_job_id": job["id"]})
    assert r.status_code == 409 and "Cancele" in r.text
