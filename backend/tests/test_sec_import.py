"""Ficheiros .sec de DVR: o original fica intacto (evidência) e a análise usa o H.264 extraído dos blocos."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest
from sqlmodel import Session, select

from app.services import sec_format

from .conftest import wait_for_job
from .fakes import RED, make_image, make_sec, make_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requer ffmpeg (para criar o H.264 de teste)")


@pytest.fixture
def sec_file(tmp_path):
    video = make_video(tmp_path / "v.mp4", [(0.5, 3.5, RED)], duration_s=4, fps=10)
    sub = tmp_path / "sub.mp4"  # substream de menor resolução, intercalado como nos DVR
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", "scale=160:120", str(sub)], check=True)
    return make_sec(tmp_path / "12000300.sec", video, sub_source=sub, garbage_after=15)


def test_parser_separates_streams_and_resyncs(sec_file, tmp_path):
    info = sec_format.inspect(sec_file)
    assert [(s["width"], s["height"], s["frames"]) for s in info["streams"]] == [(320, 240, 40), (160, 120, 40)]
    assert info["streams"][0]["keyframes"] >= 4 and info["resyncs"] == 1 and info["skipped_bytes"] == 1000
    assert info["first_blocks"][0]["header_hex"].startswith("00 e0 02 03 01")
    out, x = sec_format.extract(sec_file, tmp_path / "a")
    assert out.suffix == ".mp4" and x["frames"] == 40 and x["fps"] == 10 and x["fps_source"] == "SPS (VUI)"
    expected = hashlib.sha256(b"".join(p for b, p in sec_format.iter_blocks(sec_file) if b.width == 320))
    assert x["h264_sha256"] == expected.hexdigest()  # só as cargas do stream principal, pela ordem, sem alterações
    _, again = sec_format.extract(sec_file, tmp_path / "b")
    assert again["h264_sha256"] == x["h264_sha256"]  # extração reproduzível


def test_without_ffmpeg_keeps_raw_h264(sec_file, tmp_path, monkeypatch):
    import cv2

    real = shutil.which
    monkeypatch.setattr(sec_format.shutil, "which", lambda n: None if n in ("ffmpeg", "ffprobe") else real(n))
    out, x = sec_format.extract(sec_file, tmp_path / "a")
    assert out.suffix == ".h264" and x["container"] == "h264" and x["fps"] == 25 and "recurso" in x["fps_source"]
    cap, n = cv2.VideoCapture(str(out)), 0
    while cap.grab():
        n += 1
    assert n == 40


def test_sec_upload_is_analysed_and_original_kept(client, sec_file, tmp_path):
    import app.db
    from app.models import AuditEvent, Video

    original = hashlib.sha256(sec_file.read_bytes()).hexdigest()
    cid = client.post("/api/cases", json={"name": "DVR .sec"}).json()["id"]
    with sec_file.open("rb") as fh:
        r = client.post(f"/api/cases/{cid}/videos",
                        files=[("files", (sec_file.name, fh, "application/octet-stream"))])
    assert r.status_code == 201, r.text
    v = r.json()[0]
    assert v["sha256"] == original and v["frame_count"] == 40 and v["fps"] == 10 and v["duration_s"] == 4
    assert v["extraction"]["method"] == sec_format.METHOD and v["analysis_sha256"]
    with Session(app.db.get_engine()) as s:
        video = s.get(Video, v["id"])
        assert hashlib.sha256(open(video.stored_path, "rb").read()).hexdigest() == original  # original intacto
        assert video.source_path == video.analysis_path != video.stored_path
        ev = s.exec(select(AuditEvent).where(AuditEvent.action == "video.extracted")).one()
        details = json.loads(ev.details) if isinstance(ev.details, str) else ev.details
        assert details["original_sha256"] == original and details["frames"] == 40
    stream = client.get(f"/api/videos/{v['id']}/stream")
    assert stream.status_code == 200 and stream.content[4:8] == b"ftyp"  # o browser recebe o MP4 extraído

    with open(make_image(tmp_path / "r.jpg", RED), "rb") as fh:
        client.post(f"/api/cases/{cid}/references", files=[("files", ("r.jpg", fh, "image/jpeg"))])
    body = {"params": {"sample_fps": 10, "threshold": 0.05, "motion_filter": False, "tracking": True}}
    job = wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json=body).json()["id"])
    assert job["status"] == "completed" and job["matches"] > 0
    det = client.get(f"/api/jobs/{job['id']}/detections").json()[0]
    frame = client.get(f"/api/detections/{det['id']}/frame/original")  # lido da cópia extraída
    assert frame.status_code == 200 and frame.headers["X-FaceAI-Extraction"] in ("seek", "vizinho")

    from app.services.evidence import verify_video

    with Session(app.db.get_engine()) as s:
        video = s.get(Video, v["id"])
        assert verify_video(video) == (True, "")
        with open(video.analysis_path, "ab") as fh:
            fh.write(b"x")
        ok, reason = verify_video(video)
        assert not ok and "cópia de análise" in reason


def test_invalid_sec_is_rejected_without_leftovers(client, tmp_path):
    import app.db

    cid = client.post("/api/cases", json={"name": "Inválido"}).json()["id"]
    bad = tmp_path / "x.sec"
    bad.write_bytes(b"\x00\xe0\x02\x03" + bytes(range(256)) * 20)
    with bad.open("rb") as fh:
        r = client.post(f"/api/cases/{cid}/videos", files=[("files", (bad.name, fh, "application/octet-stream"))])
    assert r.status_code == 422 and ".sec" in r.json()["detail"]
    assert not [p for p in app.db.settings.case_dir(cid).rglob("*") if p.is_file()]


def test_cli_sec_info(sec_file, capsys, tmp_path):
    from app import cli

    cli.main(["sec-info", str(sec_file)])
    info = json.loads(capsys.readouterr().out)
    assert info["streams"][0]["frames"] == 40
    cli.main(["sec-info", str(sec_file), "--extract", str(tmp_path / "out")])
    assert (tmp_path / "out.mp4").exists()

