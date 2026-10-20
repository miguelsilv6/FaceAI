"""O perfil de rigor da Nova análise fica registado nos parâmetros e no relatório."""

from __future__ import annotations

from .conftest import wait_for_job
from .fakes import RED, make_image, make_video


def test_preset_recorded_and_in_report(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Rigor"}).json()["id"]
    path = make_video(tmp_path / "v.mp4", [(0.5, 1.5, RED)], duration_s=2, fps=10)
    with path.open("rb") as fh:
        client.post(f"/api/cases/{cid}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    with make_image(tmp_path / "ref.jpg", RED).open("rb") as fh:
        client.post(f"/api/cases/{cid}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    params = {"preset": "thorough", "sample_fps": 5, "motion_filter": False, "cascade": False, "threshold": 0.05}
    job = wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json={"params": params}).json()["id"])
    assert job["params"]["preset"] == "thorough"
    assert "minuciosa" in client.get(f"/api/jobs/{job['id']}/export/html").text
    bad = client.post(f"/api/cases/{cid}/jobs", json={"params": {"preset": "turbo"}})
    assert bad.status_code == 422
