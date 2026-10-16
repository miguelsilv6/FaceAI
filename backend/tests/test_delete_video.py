"""Eliminar um vídeo importado: só sem análises que o usem, com confirmação e registo na auditoria."""

from __future__ import annotations

from pathlib import Path

from .conftest import new_client, wait_for_job
from .fakes import RED, make_image, make_video

PW = "girassol-trovoada-marmelo-9"


def _upload(client, case_id, tmp_path, name):
    path = make_video(tmp_path / name, [(0.5, 1.5, RED)], duration_s=2, fps=10)
    with path.open("rb") as fh:
        return client.post(f"/api/cases/{case_id}/videos", files=[("files", (name, fh, "video/mp4"))]).json()[0]


def _stored_path(video_id):
    from sqlmodel import Session

    import app.db
    from app.models import Video

    with Session(app.db.get_engine()) as s:
        return Path(s.get(Video, video_id).stored_path)


def test_delete_unused_video(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Vídeos"}).json()
    v = _upload(client, case["id"], tmp_path, "errado.mp4")
    path = _stored_path(v["id"])
    assert path.exists()
    # Confirmação errada → 422
    r = client.request("DELETE", f"/api/videos/{v['id']}", json={"confirm_video_id": v["id"] + 1})
    assert r.status_code == 422
    r = client.request("DELETE", f"/api/videos/{v['id']}", json={"confirm_video_id": v["id"], "reason": "duplicado"})
    assert r.status_code == 204
    assert client.get(f"/api/cases/{case['id']}/videos").json() == []
    assert not path.exists()
    events = [e for e in client.get(f"/api/cases/{case['id']}/audit").json() if e["action"] == "video.deleted"]
    assert events and events[0]["details"]["filename"] == "errado.mp4" and events[0]["details"]["reason"] == "duplicado"


def test_video_used_by_analysis_cannot_be_deleted(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Com análise"}).json()
    img = make_image(tmp_path / "ref.jpg", RED)
    with img.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    v = _upload(client, case["id"], tmp_path, "cam.mp4")
    job = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {"sample_fps": 2, "threshold": 0.05}}).json()
    wait_for_job(client, job["id"])
    r = client.request("DELETE", f"/api/videos/{v['id']}", json={"confirm_video_id": v["id"]})
    assert r.status_code == 409 and f"#{job['id']}" in r.json()["detail"]
    # Depois de eliminar a análise, já pode
    client.request("DELETE", f"/api/jobs/{job['id']}", json={"confirm_job_id": job["id"]})
    assert client.request("DELETE", f"/api/videos/{v['id']}", json={"confirm_video_id": v["id"]}).status_code == 204


def test_only_case_manager_can_delete_video(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Permissões"}).json()
    v = _upload(client, case["id"], tmp_path, "cam.mp4")
    ana = new_client("ana.video", PW)
    assert ana.request("DELETE", f"/api/videos/{v['id']}", json={"confirm_video_id": v["id"]}).status_code == 404
    client.post(f"/api/cases/{case['id']}/members", json={"username": "ana.video"})
    assert ana.request("DELETE", f"/api/videos/{v['id']}", json={"confirm_video_id": v["id"]}).status_code == 403
