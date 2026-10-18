"""Remoção dos originais convertidos: só com uma cópia de análise sem perdas verificada; SHA-256 guardado; reanexar."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.services import originals, sec_format

from .conftest import wait_for_job
from .fakes import RED, make_image, make_sec, make_video
from .test_sec_import import wait_extracted

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="requer ffmpeg/ffprobe")


@pytest.fixture
def removing(monkeypatch):
    from app.services import transcode

    monkeypatch.setattr(originals, "enabled", lambda: True)
    monkeypatch.setattr(transcode, "ffmpeg_available", lambda: True)


def _upload(client, cid, path, mime="application/octet-stream"):
    with path.open("rb") as fh:
        r = client.post(f"/api/cases/{cid}/videos", files=[("files", (path.name, fh, mime))])
    assert r.status_code == 201, r.text
    return r.json()[0]


def _events(action):
    import app.db
    from app.models import AuditEvent

    with Session(app.db.get_engine()) as s:
        return [json.loads(e.details) if isinstance(e.details, str) else e.details
                for e in s.exec(select(AuditEvent).where(AuditEvent.action == action))]


def _video(vid):
    import app.db
    from app.models import Video

    with Session(app.db.get_engine()) as s:
        return s.get(Video, vid)


def _analyse(client, cid, tmp_path):
    with open(make_image(tmp_path / "r.jpg", RED), "rb") as fh:
        client.post(f"/api/cases/{cid}/references", files=[("files", ("r.jpg", fh, "image/jpeg"))])
    body = {"params": {"sample_fps": 10, "threshold": 0.05, "motion_filter": False, "tracking": True}}
    return wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json=body).json()["id"], timeout=60)


@pytest.fixture
def sec_file(tmp_path):
    video = make_video(tmp_path / "v.mp4", [(0.5, 3.5, RED)], duration_s=4, fps=10)
    return make_sec(tmp_path / "12000300.sec", video)


def test_sec_original_removed_after_verified_extraction(client, sec_file, tmp_path, removing):
    cid = client.post("/api/cases", json={"name": "Remover"}).json()["id"]
    v = wait_extracted(client, cid, _upload(client, cid, sec_file)["id"])
    assert v["extraction_status"] == "ready" and v["original_removed_at"]
    video = _video(v["id"])
    assert not Path(video.stored_path).exists() and Path(video.analysis_path).exists()
    ev = _events("video.original_removed")[0]
    assert ev["original_sha256"] == v["sha256"] and ev["original_size_bytes"] == sec_file.stat().st_size
    assert ev["video_streamhash"] == video.analysis_streamhash
    job = _analyse(client, cid, tmp_path)
    assert job["status"] == "completed" and job["matches"] > 0
    from app.services.evidence import verify_video

    ok, reason = verify_video(video)
    assert ok and "original removido" in reason
    html = client.get(f"/api/jobs/{job['id']}/export/html").text
    assert "Original removido do servidor" in html
    assert client.post(f"/api/videos/{v['id']}/extract").status_code == 409  # não falhou (e não há original)


def test_fps_correction_without_original(client, sec_file, removing, monkeypatch):
    from app.services.video_processor import probe

    monkeypatch.setattr(sec_format, "_sps_fps", lambda path: None)  # DVR sem fps no SPS: assume 25
    cid = client.post("/api/cases", json={"name": "fps sem original"}).json()["id"]
    v = wait_extracted(client, cid, _upload(client, cid, sec_file)["id"])
    assert v["fps"] == 25 and v["original_removed_at"]
    r = client.post(f"/api/videos/{v['id']}/fps", json={"fps": 10, "reason": "o DVR grava a 10 fps"})
    assert r.status_code == 200, r.text
    fixed = wait_extracted(client, cid, v["id"])
    assert fixed["fps"] == 10 and fixed["duration_s"] == 4 and not fixed["extraction_error"]
    assert fixed["extraction"]["h264_sha256"] == v["extraction"]["h264_sha256"]
    copy = _video(v["id"]).analysis_path
    assert probe(copy).frame_count == 40 and originals.annexb_sha256(copy)[0] == v["extraction"]["h264_sha256"]


def test_reupload_reattaches_original(client, sec_file, removing):
    cid = client.post("/api/cases", json={"name": "Reanexar"}).json()["id"]
    v = wait_extracted(client, cid, _upload(client, cid, sec_file)["id"])
    assert v["original_removed_at"]
    again = _upload(client, cid, sec_file)
    assert again["id"] == v["id"] and again["original_removed_at"] is None
    assert Path(_video(v["id"]).stored_path).read_bytes() == sec_file.read_bytes()
    assert len(client.get(f"/api/cases/{cid}/videos").json()) == 1 and _events("video.original_reattached")
    # o mesmo ficheiro outra vez (original presente): duplicado, como antes
    with sec_file.open("rb") as fh:
        r = client.post(f"/api/cases/{cid}/videos", files=[("files", (sec_file.name, fh, "application/octet-stream"))])
    assert r.status_code == 409


def test_other_format_gets_lossless_remux_then_original_removed(client, tmp_path, removing):
    avi = make_video(tmp_path / "cam.avi", [(0.5, 3.5, RED)], duration_s=4, fps=10)  # MPEG-4: o browser não abre
    original_hash = originals.stream_hash(avi)
    keep = client.post("/api/cases", json={"name": "Sem remoção"}).json()["id"]
    base = _upload(client, keep, avi, "video/x-msvideo")  # com a remoção desligada para comparar
    cid = client.post("/api/cases", json={"name": "Remux"}).json()["id"]
    v = wait_extracted(client, cid, _upload(client, cid, avi, "video/x-msvideo")["id"])
    video = _video(v["id"])
    assert v["extraction"]["method"] == originals.REMUX_METHOD and video.analysis_path.endswith(".mkv")
    assert originals.stream_hash(video.analysis_path) == original_hash  # os mesmos pacotes de vídeo
    assert v["original_removed_at"] and not Path(video.stored_path).exists()
    assert v["frame_count"] == base["frame_count"] and v["fps"] == base["fps"]
    job = _analyse(client, cid, tmp_path)
    assert job["matches"] > 0
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(originals, "enabled", lambda: False)
        from app.services import transcode

        mp.setattr(transcode, "ffmpeg_available", lambda: False)
        base_job = _analyse(client, keep, tmp_path)
    assert base_job["matches"] == job["matches"] and base_job["faces_detected"] == job["faces_detected"]


def test_browser_playable_mp4_is_never_removed(client, tmp_path, removing):
    src = make_video(tmp_path / "src.mp4", [(0.5, 1.5, RED)], duration_s=2, fps=10)
    h264 = tmp_path / "h264.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(h264)], check=True)
    cid = client.post("/api/cases", json={"name": "MP4"}).json()["id"]
    v = _upload(client, cid, h264, "video/mp4")
    assert v["extraction_status"] == "" and v["original_removed_at"] is None
    assert Path(_video(v["id"]).stored_path).exists()


def test_failed_verification_keeps_original(client, sec_file, removing, monkeypatch):
    monkeypatch.setattr(originals, "verify_copy", lambda video: (False, "verificação simulada falhou"))
    cid = client.post("/api/cases", json={"name": "Manter"}).json()["id"]
    v = wait_extracted(client, cid, _upload(client, cid, sec_file)["id"])
    assert v["extraction_status"] == "ready" and v["original_removed_at"] is None
    assert Path(_video(v["id"]).stored_path).exists()
    assert _events("video.original_kept")[0]["reason"] == "verificação simulada falhou"


def test_cli_remove_originals(client, tmp_path, capsys, monkeypatch):
    from app import cli
    from app.services import transcode

    avi = make_video(tmp_path / "old.avi", [(0.5, 1.5, RED)], duration_s=2, fps=10)
    cid = client.post("/api/cases", json={"name": "Antigos"}).json()["id"]
    v = _upload(client, cid, avi, "video/x-msvideo")  # importado sem remoção (como nas versões anteriores)
    monkeypatch.setattr(transcode, "ffmpeg_available", lambda: True)
    cli.main(["remove-originals", str(cid), "--dry-run"])
    assert "1 original(is)" in capsys.readouterr().out and Path(_video(v["id"]).stored_path).exists()
    cli.main(["remove-originals", str(cid), "--yes"])
    assert "1 original(is) apagado(s)" in capsys.readouterr().out
    video = _video(v["id"])
    assert video.original_removed_at and not Path(video.stored_path).exists() and Path(video.analysis_path).exists()
