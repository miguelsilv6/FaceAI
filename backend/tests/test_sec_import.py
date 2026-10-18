"""Ficheiros .sec de DVR: o original fica intacto (evidência) e a análise usa o H.264 extraído dos blocos."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time

import pytest
from sqlmodel import Session, select

from app.services import sec_format

from .conftest import wait_for_job
from .fakes import RED, make_image, make_sec, make_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requer ffmpeg (para criar o H.264 de teste)")


def wait_extracted(client, cid, vid, timeout=60):
    """A extração corre em segundo plano: espera que o vídeo fique pronto (ou falhe)."""
    deadline = time.time() + timeout
    while True:
        v = next(x for x in client.get(f"/api/cases/{cid}/videos").json() if x["id"] == vid)
        if v["extraction_status"] in ("ready", "failed"):
            return v
        assert time.time() < deadline, v
        time.sleep(0.05)


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
    assert r.json()[0]["extraction_status"] in ("pending", "running", "ready")
    v = wait_extracted(client, cid, r.json()[0]["id"])
    assert v["extraction_status"] == "ready" and v["extraction_progress"] == 1
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



def _import_sec(client, path, name="fps"):
    cid = client.post("/api/cases", json={"name": name}).json()["id"]
    with path.open("rb") as fh:
        r = client.post(f"/api/cases/{cid}/videos", files=[("files", (path.name, fh, "application/octet-stream"))])
    assert r.status_code == 201, r.text
    return cid, wait_extracted(client, cid, r.json()[0]["id"])


def test_fps_correction_retimes_analysis_and_keeps_h264(client, sec_file, tmp_path, monkeypatch):
    import app.db
    from app.models import AuditEvent, Video
    from app.services.video_processor import probe

    monkeypatch.setattr(sec_format, "_sps_fps", lambda path: None)  # DVR sem fps no SPS: assume 25
    cid, v = _import_sec(client, sec_file)
    assert v["fps"] == 25 and v["duration_s"] == 1.6 and v["extraction"]["fps_assumed"] is True
    with open(make_image(tmp_path / "r.jpg", RED), "rb") as fh:
        client.post(f"/api/cases/{cid}/references", files=[("files", ("r.jpg", fh, "image/jpeg"))])
    body = {"params": {"sample_fps": 25, "threshold": 0.05, "motion_filter": False, "tracking": True}}
    job = wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json=body).json()["id"])
    assert job["matches"] > 0
    tracks_before = client.get(f"/api/jobs/{job['id']}/tracks").json()
    r = client.put(f"/api/videos/{v['id']}/clock", json={"video_s": 0.4, "dvr_time": "2025-11-27T12:00:00"})
    assert r.status_code == 200

    r = client.post(f"/api/videos/{v['id']}/fps", json={"fps": 10, "reason": "o DVR grava a 10 fps"})
    assert r.status_code == 200, r.text
    assert r.json()["extraction_status"] in ("pending", "running", "ready")
    fixed = wait_extracted(client, cid, v["id"])
    x = fixed["extraction"]
    assert fixed["fps"] == 10 and fixed["duration_s"] == 4 and fixed["frame_count"] == 40
    assert x["h264_sha256"] == v["extraction"]["h264_sha256"] and fixed["analysis_sha256"] != v["analysis_sha256"]
    assert x["fps_assumed"] is False and x["fps_history"][0]["from"] == 25 and "manual" in x["fps_source"]
    with Session(app.db.get_engine()) as s:
        video = s.get(Video, v["id"])
        assert probe(video.analysis_path).fps == 10
        ev = s.exec(select(AuditEvent).where(AuditEvent.action == "video.fps_corrected")).one()
        details = json.loads(ev.details) if isinstance(ev.details, str) else ev.details
        assert details["jobs"] == [job["id"]] and details["detections_retimed"] > 0
    dets = client.get(f"/api/jobs/{job['id']}/detections").json()
    assert all(abs(d["timestamp_s"] - d["frame_index"] / 10) < 1e-6 for d in dets)
    after = client.get(f"/api/jobs/{job['id']}/tracks").json()
    assert [round(t["start_s"] * 2.5, 3) for t in tracks_before] == [round(t["start_s"], 3) for t in after]
    # ponto manual: o frame que mostrava 12:00:00 (0,4 s a 25 fps = frame 10) continua a mostrá-lo, agora a 1,0 s
    assert fixed["dvr_rate"] == 1 and fixed["time_calibration"]["video_s"] == 1.0
    assert fixed["dvr_start"] == "2025-11-27T11:59:59.000"
    html = client.get(f"/api/jobs/{job['id']}/export/html").text
    assert "fps corrigido de 25 para 10" in html and "fps assumido" not in html


def test_fps_correction_from_dvr_clock_suggestion(client, sec_file, monkeypatch):
    import app.db
    from app.models import Video
    from app.services import fps_fix

    monkeypatch.setattr(sec_format, "_sps_fps", lambda path: None)
    cid, v = _import_sec(client, sec_file)
    with Session(app.db.get_engine()) as s:  # o OCR leu o relógio: avança 2,5 s por cada segundo do vídeo
        video = s.get(Video, v["id"])
        video.dvr_start, video.dvr_rate, video.time_source = "2025-11-27T12:00:00.000", 2.5, "ocr"
        video.time_calibration = {"rate": 2.5, "samples": [{"video_s": 1.0, "text": "12:00:02"}]}
        fps_fix.suggest_from_clock(video, 2.5)
        s.add(video)
        s.commit()
        assert video.extraction["suggested_fps"]["fps"] == 10
    r = client.post(f"/api/videos/{v['id']}/fps", json={"fps": 10, "reason": "relógio do DVR", "source": "ocr"})
    assert r.status_code == 200, r.text
    fixed = wait_extracted(client, cid, v["id"])
    assert fixed["dvr_rate"] == 1 and fixed["time_calibration"]["samples"][0]["video_s"] == 2.5
    assert "suggested_fps" not in fixed["extraction"] and "OCR" in fixed["extraction"]["fps_source"]


def test_fps_correction_is_refused_when_not_allowed(client, sec_file, tmp_path):
    import app.db
    from app.models import Job, JobStatus

    cid, v = _import_sec(client, sec_file)
    assert client.post(f"/api/videos/{v['id']}/fps", json={"fps": 200, "reason": "teste"}).status_code == 422
    with Session(app.db.get_engine()) as s:
        s.add(Job(case_id=cid, video_ids=[v["id"]], status=JobStatus.running))
        s.commit()
    r = client.post(f"/api/videos/{v['id']}/fps", json={"fps": 10, "reason": "teste"})
    assert r.status_code == 409
    mp4 = make_video(tmp_path / "n.mp4", [(0.5, 1.5, RED)], duration_s=2, fps=10)
    with mp4.open("rb") as fh:
        other = client.post(f"/api/cases/{cid}/videos", files=[("files", ("n.mp4", fh, "video/mp4"))]).json()[0]
    assert client.post(f"/api/videos/{other['id']}/fps", json={"fps": 12, "reason": "teste"}).status_code == 422


def test_restart_copy_requeues_a_running_conversion(client, sec_file):
    import app.db
    from app.models import Video
    from app.services import transcode

    _, v = _import_sec(client, sec_file)
    with Session(app.db.get_engine()) as s:
        video = s.get(Video, v["id"])
        transcode._queued.add(video.id)  # simula uma conversão em fila
        try:
            assert transcode.restart_copy(s, video) == transcode.PENDING
            assert video.id in transcode._requeue and video.id in transcode._cancelled
        finally:
            transcode._queued.discard(video.id)
            transcode._requeue.discard(video.id)
            transcode._cancelled.discard(video.id)


def test_other_channel_and_payload_prefix_are_read(tmp_path):
    """O byte 3 do cabeçalho é o canal (…0400.sec → 04); algumas cargas trazem bytes antes do start code."""
    from .fakes import h264_access_units, sec_block

    video = make_video(tmp_path / "v.mp4", [(0.5, 1.5, RED)], duration_s=2, fps=10)
    sec = make_sec(tmp_path / "21000400.sec", video, channel=4)
    assert sec_format.is_sec(sec)
    info = sec_format.inspect(sec)
    assert info["channels"] == [4] and info["streams"][0]["frames"] == 20
    blocks = [p for _, p in sec_format.iter_blocks(sec)]
    padded = tmp_path / "pad.sec"
    padded.write_bytes(b"".join(sec_block(b"\x00\x10\x20\x30" + p, i % 10 == 0, 320, 240, i, 5)
                                for i, p in enumerate(blocks)))
    stats = {}
    assert [p for _, p in sec_format.iter_blocks(padded, stats)] == blocks and stats["payload_prefix_bytes"] == 80
    assert h264_access_units  # (o gerador de teste continua disponível)


def test_extraction_failure_keeps_original_and_can_be_retried(client, sec_file, monkeypatch):
    import app.db
    from app.models import AuditEvent, Video

    real = sec_format._free_space
    monkeypatch.setattr(sec_format, "_free_space", lambda folder: 1000)
    cid = client.post("/api/cases", json={"name": "Sem espaço"}).json()["id"]
    with sec_file.open("rb") as fh:
        r = client.post(f"/api/cases/{cid}/videos", files=[("files", (sec_file.name, fh, "application/octet-stream"))])
    assert r.status_code == 201  # o upload termina logo; a extração corre depois
    v = wait_extracted(client, cid, r.json()[0]["id"])
    assert v["extraction_status"] == "failed" and "espaço insuficiente" in v["extraction_error"]
    with Session(app.db.get_engine()) as s:
        video = s.get(Video, v["id"])
        assert open(video.stored_path, "rb").read() == sec_file.read_bytes()  # original guardado e intacto
        assert s.exec(select(AuditEvent).where(AuditEvent.action == "video.extraction_failed")).first()
    body = {"params": {"sample_fps": 10, "threshold": 0.05, "motion_filter": False}}
    job = client.post(f"/api/cases/{cid}/jobs", json={"params": {**body["params"], "mode": "discover"}})
    assert job.status_code == 409 and "extração" in job.json()["detail"]
    assert client.get(f"/api/videos/{v['id']}/stream").status_code == 409
    monkeypatch.setattr(sec_format, "_free_space", real)
    assert client.post(f"/api/videos/{v['id']}/extract").status_code == 200
    v = wait_extracted(client, cid, v["id"])
    assert v["extraction_status"] == "ready" and v["frame_count"] == 40 and not v["extraction_error"]
    assert client.post(f"/api/videos/{v['id']}/extract").status_code == 409  # já não falhou


def test_single_write_and_resume_after_restart(client, sec_file, tmp_path):
    import app.db
    from app.models import Video
    from app.services import sec_extract

    cid, v = _import_sec(client, sec_file)
    with Session(app.db.get_engine()) as s:
        video = s.get(Video, v["id"])
        folder = __import__("pathlib").Path(video.stored_path).parent
        assert not list(folder.glob("*.h264")) and not list(folder.glob("*.part*"))  # sem o .h264 intermédio
        video.extraction_status = "running"  # o servidor parou a meio da extração
        s.add(video)
        s.commit()
    sec_extract.resume_pending_extractions()
    assert wait_extracted(client, cid, v["id"])["extraction_status"] == "ready"


def test_invalid_sec_reports_first_bytes(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Inválido 2"}).json()["id"]
    bad = tmp_path / "x.sec"
    bad.write_bytes(b"ABCD" * 100)
    with bad.open("rb") as fh:
        r = client.post(f"/api/cases/{cid}/videos", files=[("files", (bad.name, fh, "application/octet-stream"))])
    assert r.status_code == 422 and "41 42 43 44" in r.json()["detail"]
    assert client.get(f"/api/cases/{cid}/videos").json() == []
