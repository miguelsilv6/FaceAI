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


def _open(client, video_id, fmt="mp4"):
    """O que o leitor faz ao abrir um vídeo "on_demand": pede a cópia."""
    r = client.post(f"/api/videos/{video_id}/playback", params={"format": fmt})
    assert r.status_code == 200 and r.json()["playback_status"] in ("pending", "running", "ready"), r.text
    return r.json()


def _h264(path, pix_fmt="yuv420p", duration_s=2.0):
    import subprocess
    src = make_video(path.with_suffix(".src.mp4"), [(0.0, 2.0, RED)], duration_s=duration_s, fps=10)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-pix_fmt", pix_fmt,
                    str(path)], check=True)
    return path


def test_mpeg4_gets_playback_copy(client, tmp_path, with_ffmpeg):
    client.post("/api/cases", json={"name": "c"})
    video = make_video(tmp_path / "dvr.mp4", [(0.0, 3.0, RED)], duration_s=3, fps=10)  # MPEG-4 Part 2 (mp4v)
    v = _upload(client, video, "dvr.mp4")
    assert v["playback_status"] == "on_demand"  # só é criada quando o vídeo for aberto
    time.sleep(0.5)
    assert not transcode._queued
    _open(client, v["id"])
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
    assert v444["playback_status"] == "on_demand"
    _open(client, v444["id"])
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
    _open(client, v["id"])
    v = _wait_status(client, v["id"], {"failed", "ready"})
    assert v["playback_status"] == "failed" and v["playback_error"]
    assert any(e["action"] == "video.playback_copy_failed" for e in client.get("/api/cases/1/audit").json())


def test_without_ffmpeg_status_is_unavailable(client, tmp_path):
    client.post("/api/cases", json={"name": "c"})
    v = _upload(client, make_video(tmp_path / "a.avi", [(0.0, 1.0, RED)], duration_s=1, fps=10), "a.avi")
    assert v["playback_status"] == "unavailable" and "ffmpeg" in v["playback_error"]


def test_resume_pending_copies(client, tmp_path, with_ffmpeg):
    """No arranque: conversões interrompidas a meio são retomadas; vídeos de versões anteriores (sem estado) e
    pedidos que nunca começaram ficam "on_demand" (a cópia é criada quando forem abertos)."""
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
        s.add(Video(case_id=1, filename="queued.mp4", stored_path=str(dest), size_bytes=1, duration_s=2, fps=10,
                    codec="mp4v", playback_status="pending"))
        s.commit()
    transcode.resume_pending_copies()
    assert _wait_status(client, 2, {"ready", "failed"})["playback_status"] == "ready"
    assert _wait_status(client, 1, {"on_demand"}) and _wait_status(client, 3, {"on_demand"})
    assert np.asarray(cv2.VideoCapture(str(tmp_path / "stored_old_playback.mp4")).read()[1]).size > 0


def test_raw_h264_with_mp4_extension_needs_copy(client, tmp_path, with_ffmpeg):
    """Exportações de DVR com extensão .mp4 que são H.264 "cru": o codec é h264 mas o browser não as abre."""
    import subprocess

    client.post("/api/cases", json={"name": "c"})
    src = make_video(tmp_path / "s.mp4", [(0.0, 2.0, RED)], duration_s=2, fps=10)
    raw = tmp_path / "raw.h264"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-f", "h264", str(raw)], check=True)
    fake_mp4 = tmp_path / "dvr_export.mp4"
    raw.rename(fake_mp4)
    v = _upload(client, fake_mp4, "dvr_export.mp4")
    assert v["playback_status"] == "on_demand", v
    _open(client, v["id"])
    probe = client.get(f"/api/videos/{v['id']}/probe").json()
    assert probe["codec_name"] == "h264" and probe["format_name"] == "h264"
    assert _wait_status(client, v["id"], {"ready", "failed"})["playback_status"] == "ready"


def test_webm_copy_for_browsers_without_h264(client, tmp_path, with_ffmpeg):
    client.post("/api/cases", json={"name": "c"})
    v = _upload(client, _h264(tmp_path / "ok.mp4"), "ok.mp4")
    assert v["playback_status"] == "not_needed"
    assert client.post(f"/api/videos/{v['id']}/playback", params={"format": "avi"}).status_code == 422
    r = client.post(f"/api/videos/{v['id']}/playback", params={"format": "webm"})
    assert r.status_code == 200 and r.json()["playback_format"] == "webm"
    v = _wait_status(client, v["id"], {"ready", "failed"})
    assert v["playback_status"] == "ready", v
    stream = client.get(f"/api/videos/{v['id']}/stream")
    assert stream.headers["content-type"] == "video/webm" and stream.content[:4] == b"\x1aE\xdf\xa3"  # EBML
    assert stream.headers["cache-control"] == "no-cache"  # o mesmo URL passa do original para a cópia


def test_progress_parsing():
    assert transcode._progress_seconds("out_time_us", "2500000") == 2.5
    assert transcode._progress_seconds("out_time_ms", "1000000") == 1.0
    assert transcode._progress_seconds("out_time", "00:01:02.500000") == 62.5
    assert transcode._progress_seconds("out_time_us", "N/A") is None
    assert transcode._progress_seconds("frame", "10") is None


def test_case_wide_copies_and_delete_while_converting(client, tmp_path, with_ffmpeg):
    """O browser não reproduz um vídeo → cópias de todos os vídeos do caso; eliminar o caso cancela as conversões."""
    client.post("/api/cases", json={"name": "c"})
    a = _upload(client, _h264(tmp_path / "a.mp4"), "a.mp4")
    b = _upload(client, _h264(tmp_path / "b.mp4", duration_s=2.5), "b.mp4")  # ficheiro distinto do "a"
    assert a["playback_status"] == b["playback_status"] == "not_needed"
    # H.264/MP4 que o browser já reproduz: uma cópia MP4 não é necessária (as conversões só se fazem se precisas)
    r = client.post("/api/cases/1/playback", params={"format": "mp4"})
    assert r.status_code == 200 and {v["playback_status"] for v in r.json()} == {"not_needed"}
    # Browser sem H.264 (ex.: alguns Linux): cópias VP9/WebM de todos os vídeos
    r = client.post("/api/cases/1/playback", params={"format": "webm"})
    assert r.status_code == 200 and {v["playback_status"] for v in r.json()} <= {"pending", "running", "ready"}
    for vid in (a["id"], b["id"]):
        assert _wait_status(client, vid, {"ready", "failed"})["playback_status"] == "ready"
    # Repetir não volta a converter os que já têm a cópia neste formato.
    again = client.post("/api/cases/1/playback", params={"format": "webm"}).json()
    assert {v["playback_status"] for v in again} == {"ready"}

    # Conversão longa em curso + caso eliminado: o ffmpeg é cancelado e nada é recriado na pasta do caso.
    long_src = make_video(tmp_path / "long.mp4", [(0.0, 60.0, RED)], duration_s=60, fps=25)
    c = _upload(client, long_src, "long.mp4")
    assert c["playback_status"] == "on_demand"
    _open(client, c["id"])
    _wait_status(client, c["id"], {"running", "ready"})
    from app.config import settings
    folder = settings.case_dir(1)
    assert client.request("DELETE", "/api/cases/1", json={"confirm_case_id": 1}).status_code == 204
    time.sleep(1.5)
    assert not folder.exists()
    assert not transcode._running and c["id"] not in transcode._queued


def test_light_copy_and_expiry(client, tmp_path, with_ffmpeg):
    """A cópia é leve (≤ 720 p, tamanho de apresentação, SAR 1:1) e expira sem uso; a evidência fica."""
    import json
    import subprocess
    from datetime import UTC, datetime, timedelta

    from sqlmodel import Session

    from app.db import get_engine
    from app.models import Video

    client.post("/api/cases", json={"name": "c"})
    src = make_video(tmp_path / "s.mp4", [(0.0, 2.0, RED)], duration_s=2, fps=10)
    big = tmp_path / "big.avi"  # 1080p MPEG-4 com píxeis não quadrados (960×1080 → 1920×1080)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=960:1080,setsar=1",
                    "-c:v", "mpeg4", str(big)], check=True)
    v = _upload(client, big, "big.avi")
    assert v["aspect"]["display"] == [1920, 1080] and v["playback_status"] == "on_demand"
    _open(client, v["id"])
    v = _wait_status(client, v["id"], {"ready", "failed"})
    assert v["playback_status"] == "ready", v
    out = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                     "stream=width,height,sample_aspect_ratio", "-of", "json", v["playback_path"]],
                                    capture_output=True, check=True).stdout)["streams"][0]
    assert (out["width"], out["height"]) == (1280, 720) and out.get("sample_aspect_ratio", "1:1") == "1:1"

    assert client.get(f"/api/videos/{v['id']}/stream").status_code == 200
    with Session(get_engine()) as s:
        assert s.get(Video, v["id"]).playback_used_at is not None
    assert transcode.expire_unused_copies() == {}  # usada agora: fica
    assert transcode.expire_unused_copies(datetime.now(UTC) + timedelta(days=99), ttl_days=0) == {}  # 0 = nunca
    done = transcode.expire_unused_copies(datetime.now(UTC) + timedelta(days=15), ttl_days=14)
    assert done[1]["copies"] == 1 and done[1]["bytes"] > 0
    v2 = next(x for x in client.get("/api/cases/1/videos").json() if x["id"] == v["id"])
    assert v2["playback_status"] == "on_demand" and v2["playback_path"] is None
    from pathlib import Path
    assert not Path(v["playback_path"]).exists() and Path(v["stored_path"]).exists()
    assert any(e["action"] == "storage.playback_expired" for e in client.get("/api/cases/1/audit").json())
