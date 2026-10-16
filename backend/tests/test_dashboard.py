"""Painel do caso: revisão, correspondências por câmara e por hora real."""

from __future__ import annotations

from .conftest import new_client, wait_for_job
from .fakes import RED, make_image, make_video

PW = "girassol-trovoada-marmelo-9"


def _video(client, case_id, tmp_path, name, timeline):
    path = make_video(tmp_path / name, timeline, duration_s=6, fps=10)
    with path.open("rb") as fh:
        return client.post(f"/api/cases/{case_id}/videos", files=[("files", (name, fh, "video/mp4"))]).json()[0]


def test_dashboard_counts(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Painel"}).json()
    img = make_image(tmp_path / "ref.jpg", RED)
    with img.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    entrada = _video(client, case["id"], tmp_path, "entrada.mp4", [(1.0, 2.0, RED), (4.0, 5.0, RED)])
    rua = _video(client, case["id"], tmp_path, "rua.mp4", [(1.0, 2.0, RED)])
    client.put(f"/api/videos/{entrada['id']}/clock", json={"video_s": 0, "dvr_time": "2026-10-01T10:59:58"})
    job = client.post(f"/api/cases/{case['id']}/jobs",
                      json={"params": {"sample_fps": 2, "threshold": 0.05, "motion_filter": False, "tracking": True,
                                       "track_gap_s": 0.5}}).json()
    wait_for_job(client, job["id"])
    tracks = [t for t in client.get(f"/api/jobs/{job['id']}/tracks").json() if t["is_match"]]
    t_entrada = sorted((t for t in tracks if t["video_id"] == entrada["id"]), key=lambda t: t["start_s"])
    client.patch(f"/api/tracks/{t_entrada[0]['id']}/review", json={"status": "confirmed"})
    client.patch(f"/api/tracks/{t_entrada[1]['id']}/review", json={"status": "rejected"})

    d = client.get(f"/api/cases/{case['id']}/dashboard").json()
    dets = [x for x in client.get(f"/api/jobs/{job['id']}/detections").json() if x["kind"] != "body"]
    matches = [x for x in dets if x["is_match"]]
    r = d["review"]
    assert r["matches"] == len(matches) and r["matches"] == r["confirmed"] + r["rejected"] + r["pending"]
    assert r["confirmed"] > 0 and r["rejected"] > 0 and r["pending"] > 0
    assert (r["tracks"], r["tracks_confirmed"], r["tracks_rejected"]) == (3, 1, 1)
    cams = {c["filename"]: c for c in d["per_camera"]}
    assert cams["entrada.mp4"]["synced"] and not cams["rua.mp4"]["synced"]
    assert cams["rua.mp4"]["matches"] == cams["rua.mp4"]["pending"] > 0
    assert cams["entrada.mp4"]["first"].startswith("2026-10-01T10:59")
    # Hora real: o 1.º aparecimento é às 10:59, o 2.º já às 11:00; os de "rua" (sem relógio) ficam à parte
    assert [h["hour"] for h in d["per_hour"]] == ["2026-10-01 10", "2026-10-01 11"]
    assert d["unsynced_matches"] == cams["rua.mp4"]["matches"]
    assert d["material"]["videos"] == 2 and d["material"]["videos_synced"] == 1
    assert d["jobs"]["by_status"] == {"completed": 1} and d["jobs_considered"] == [job["id"]]
    assert d["pending_by_job"][0]["job_id"] == job["id"]
    assert any(e["action"] == "track.reviewed" or e["action"].startswith("track") for e in d["recent"])
    # job_ids filtra; sem acesso → 404
    assert client.get(f"/api/cases/{case['id']}/dashboard", params={"job_ids": "999"}).json()["review"]["matches"] == 0
    assert new_client("zeca.painel", PW).get(f"/api/cases/{case['id']}/dashboard").status_code == 404
