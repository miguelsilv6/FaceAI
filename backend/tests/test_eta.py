"""Tempo restante estimado das análises."""

from app.services.jobs import EtaEstimator


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_eta_uses_recent_rate():
    clock = Clock()
    eta = EtaEstimator(total=1000, clock=clock)
    clock.t = 2
    assert eta.update(20) is None  # pouco tempo decorrido: sem estimativa
    clock.t = 10
    assert eta.update(100) == 90.0  # 10 frames/s → 900 frames em 90 s
    # A fase seguinte é mais lenta (2 frames/s): a janela móvel acompanha a mudança.
    for t in range(20, 141, 10):
        clock.t = t
        last = eta.update(100 + (t - 10) * 2)
    assert last == (1000 - 360) / 2.0  # ritmo recente = 2 frames/s


def test_job_eta_cleared_on_finish(client, tmp_path):
    from .conftest import wait_for_job
    from .fakes import RED, make_image, make_video

    case = client.post("/api/cases", json={"name": "c"}).json()
    with make_image(tmp_path / "r.jpg", RED).open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("r.jpg", fh, "image/jpeg"))])
    with make_video(tmp_path / "v.mp4", [(0.0, 2.0, RED)], duration_s=2, fps=10).open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    job = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {"sample_fps": 2}}).json()
    assert "eta_s" in job
    assert wait_for_job(client, job["id"])["eta_s"] is None
