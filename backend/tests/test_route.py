"""Percurso multi-câmara: aparições ordenadas pela hora real do DVR."""

from __future__ import annotations

from .conftest import wait_for_job
from .fakes import RED, make_image, make_video


def _video(client, case_id, tmp_path, name, timeline):
    path = make_video(tmp_path / name, timeline, duration_s=4, fps=10)
    with path.open("rb") as fh:
        r = client.post(f"/api/cases/{case_id}/videos", files=[("files", (name, fh, "video/mp4"))])
    return r.json()[0]


def test_route_orders_by_real_time(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Percurso"}).json()
    img = make_image(tmp_path / "ref.jpg", RED)
    with img.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    entrada = _video(client, case["id"], tmp_path, "entrada.mp4", [(1.0, 2.0, RED)])
    caixa = _video(client, case["id"], tmp_path, "caixa.mp4", [(1.0, 2.0, RED)])
    sem_hora = _video(client, case["id"], tmp_path, "armazem.mp4", [(1.0, 2.0, RED)])
    # A câmara da entrada começa às 10:00:00 e a da caixa às 09:59:00 → a caixa aparece primeiro na hora real
    assert client.put(f"/api/videos/{entrada['id']}/clock",
                      json={"video_s": 0, "dvr_time": "2026-10-01T10:00:00"}).status_code == 200
    assert client.put(f"/api/videos/{caixa['id']}/clock",
                      json={"video_s": 0, "dvr_time": "2026-10-01T09:59:00"}).status_code == 200
    r = client.post(f"/api/cases/{case['id']}/jobs",
                    json={"params": {"sample_fps": 2, "threshold": 0.05, "motion_filter": False, "tracking": True}})
    job = wait_for_job(client, r.json()["id"])

    route = client.get(f"/api/cases/{case['id']}/route").json()
    assert [s["video_filename"] for s in route["segments"]] == ["caixa.mp4", "entrada.mp4"]
    assert route["segments"][0]["real_start"].startswith("2026-10-01T09:59:01")
    assert [s["video_id"] for s in route["unsynced"]] == [sem_hora["id"]]
    assert {v["filename"]: v["synced"] for v in route["videos"]} == {
        "entrada.mp4": True, "caixa.mp4": True, "armazem.mp4": False}
    assert {v["filename"]: v["appearances"] for v in route["videos"]} == {
        "entrada.mp4": 1, "caixa.mp4": 1, "armazem.mp4": 1}

    # Só confirmadas: nada até confirmar; depois só o track confirmado
    assert client.get(f"/api/cases/{case['id']}/route", params={"only": "confirmed"}).json()["segments"] == []
    track = next(t for t in client.get(f"/api/jobs/{job['id']}/tracks").json() if t["video_id"] == entrada["id"])
    client.patch(f"/api/tracks/{track['id']}/review", json={"status": "confirmed"})
    conf = client.get(f"/api/cases/{case['id']}/route", params={"only": "confirmed"}).json()
    assert [s["video_filename"] for s in conf["segments"]] == ["entrada.mp4"]
    assert client.get(f"/api/cases/{case['id']}/route", params={"job_ids": "999"}).json()["segments"] == []
    assert client.get(f"/api/cases/{case['id']}/route", params={"only": "x"}).status_code == 422


def test_route_lists_cameras_without_appearances(client, tmp_path):
    """Câmaras da análise sem aparições também aparecem (linha vazia no percurso)."""
    case = client.post("/api/cases", json={"name": "Câmaras"}).json()
    img = make_image(tmp_path / "ref.jpg", RED)
    with img.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    com = _video(client, case["id"], tmp_path, "com.mp4", [(1.0, 2.0, RED)])
    sem = _video(client, case["id"], tmp_path, "sem.mp4", [])
    for v in (com, sem):
        client.put(f"/api/videos/{v['id']}/clock", json={"video_s": 0, "dvr_time": "2026-10-01T10:00:00"})
    r = client.post(f"/api/cases/{case['id']}/jobs",
                    json={"params": {"sample_fps": 2, "threshold": 0.05, "motion_filter": False, "tracking": True}})
    wait_for_job(client, r.json()["id"])
    route = client.get(f"/api/cases/{case['id']}/route").json()
    assert {v["filename"]: (v["synced"], v["appearances"]) for v in route["videos"]} == {
        "com.mp4": (True, 1), "sem.mp4": (True, 0)}
