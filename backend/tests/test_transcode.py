"""Cópia de reprodução H.264 para o browser: deteção da necessidade, fila, estado/progresso e retoma."""

import shutil
import time

import cv2
import numpy as np
import pytest

from app.services import transcode

from .fakes import RED, make_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="requer ffmpeg/ffprobe")


@pytest.fixture()
def with_ffmpeg(monkeypatch):
    monkeypatch.setattr(transcode, "ffmpeg_available", lambda: shutil.which("ffmpeg") is not None)


def _wait_status(client, video_id, statuses, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = next(v for v in client.get("/api/cases/1/videos").json() if v["id"] == video_id)
        if v["playback_status"] in statuses:
            return v
        time.sleep(0.2)
    raise TimeoutError(v)


def _upload(client, path, name):
    with path.open("rb") as fh:
        r = client.post("/api/cases/1/videos", files=[("files", (name, fh, "video/mp4"))])
    assert r.status_code == 201, r.text
    return r.json()[0]


def _h264(path, pix_fmt="yuv420p"):
    import subprocess
    src = make_video(path.with_suffix(".src.mp4"), [(0.0, 2.0, RED)], duration_s=2, fps=10)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-pix_fmt", pix_fmt,
                    str(path)], check=True)
    return path


def test_mpeg4_gets_playback_copy(client, tmp_path, with_ffmpeg):
    client.post("/api/cases", json={"name": "c"})
    video = make_video(tmp_path / "dvr.mp4", [(0.0, 3.0, RED)], duration_s=3, fps=10)  # MPEG-4 Part 2 (mp4v)
    v = _upload(client, video, "dvr.mp4")
    assert v["playback_status"] in ("pending", "running", "ready")
    v = _wait_status(client, v["id"], {"ready", "failed"})
    assert v["playback_status"] == "ready" and v["playback_progress"] == 1.0, v
    r = client.get(f"/api/videos/{v['id']}/stream")
    assert r.headers["content-type"] == "video/mp4"
    assert transcode.probe_stream(tmp_path / "x") is None
    actions = [e["action"] for e in client.get("/api/cases/1/audit").json()]
    assert "video.playback_copy_requested" in actions and "video.playback_copy_ready" in actions


def test_browser_compatible_video_is_not_copied(client, tmp_path, with_ffmpeg):
    client.post("/api/cases", json={"name": "c"})
    v = _upload(client, _h264(tmp_path / "ok.mp4"), "ok.mp4")
    assert v["playback_status"] == "not_needed"
    # H.264 4:4:4 tem FourCC avc1, mas os browsers não o reproduzem → precisa de cópia.
    v444 = _upload(client, _h264(tmp_path / "hi444.mp4", "yuv444p"), "hi444.mp4")
    assert v444["playback_status"] in ("pending", "running", "ready")
    _wait_status(client, v444["id"], {"ready"})
    # Pedido manual (o browser não reproduz um vídeo dado como compatível).
    r = client.post(f"/api/videos/{v['id']}/playback")
    assert r.status_code == 200 and r.json()["playback_status"] in ("pending", "running", "ready")
    _wait_status(client, v["id"], {"ready"})


def test_failed_copy_is_reported(client, tmp_path, with_ffmpeg, monkeypatch):
    client.post("/api/cases", json={"name": "c"})
    video = make_video(tmp_path / "dvr.avi", [(0.0, 2.0, RED)], duration_s=2, fps=10)
    real = transcode._transcode

    def broken(video_id):
        from sqlmodel import Session

        from app.db import get_engine
        from app.models import Video
        with Session(get_engine()) as s:
            row = s.get(Video, video_id)
            open(row.stored_path, "wb").write(b"lixo")  # o ffmpeg não consegue ler
        real(video_id)

    monkeypatch.setattr(transcode, "_transcode", broken)
    v = _upload(client, video, "dvr.avi")
    v = _wait_status(client, v["id"], {"failed", "ready"})
    assert v["playback_status"] == "failed" and v["playback_error"]
    assert any(e["action"] == "video.playback_copy_failed" for e in client.get("/api/cases/1/audit").json())


def test_without_ffmpeg_status_is_unavailable(client, tmp_path):
    client.post("/api/cases", json={"name": "c"})
    v = _upload(client, make_video(tmp_path / "a.avi", [(0.0, 1.0, RED)], duration_s=1, fps=10), "a.avi")
    assert v["playback_status"] == "unavailable" and "ffmpeg" in v["playback_error"]


def test_resume_pending_copies(client, tmp_path, with_ffmpeg):
    """Vídeos de versões anteriores (sem estado) e conversões interrompidas são retomados no arranque."""
    from sqlmodel import Session

    from app.db import get_engine
    from app.models import Video

    client.post("/api/cases", json={"name": "c"})
    path = make_video(tmp_path / "old.mp4", [(0.0, 2.0, RED)], duration_s=2, fps=10)
    dest = tmp_path / "stored_old.mp4"
    shutil.copy(path, dest)
    with Session(get_engine()) as s:
        s.add(Video(case_id=1, filename="old.mp4", stored_path=str(dest), size_bytes=1, duration_s=2, fps=10,
                    codec="mp4v"))
        s.add(Video(case_id=1, filename="cut.mp4", stored_path=str(dest), size_bytes=1, duration_s=2, fps=10,
                    codec="mp4v", playback_status="running"))
        s.commit()
    transcode.resume_pending_copies()
    for vid in (1, 2):
        assert _wait_status(client, vid, {"ready", "failed"})["playback_status"] == "ready"
    assert np.asarray(cv2.VideoCapture(str(tmp_path / "stored_old_playback.mp4")).read()[1]).size > 0
