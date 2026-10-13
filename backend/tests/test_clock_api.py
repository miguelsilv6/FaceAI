"""Hora real: calibração por OCR (RapidOCR real) sobre um vídeo com a data/hora gravada na imagem."""

from datetime import datetime, timedelta

import cv2
import numpy as np
import pytest

from .fakes import RED

pytest.importorskip("rapidocr")

START = datetime(2026, 10, 12, 14, 3, 20)


def _overlay(frame, text):
    cv2.putText(frame, text, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, text, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)


def make_cctv_with_clock(path, duration_s=120, fps=5, rate=1.05, fmt="%d-%m-%Y %H:%M:%S"):
    """O relógio do DVR avança `rate` segundos por segundo de vídeo (fps nominal ≠ fps real de gravação)."""
    rng = np.random.default_rng(0)
    bg = (rng.random((360, 640, 3)) * 100).astype(np.uint8)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (640, 360))
    for i in range(int(duration_s * fps)):
        t = i / fps
        frame = bg.copy()
        if 30 <= t < 40:
            cv2.rectangle(frame, (300, 150), (370, 220), RED, -1)
        _overlay(frame, (START + timedelta(seconds=int(t * rate))).strftime(fmt))
        w.write(frame)
    w.release()
    return path


def _setup(client, tmp_path, **kw):
    case = client.post("/api/cases", json={"name": "Relógio"}).json()
    video = make_cctv_with_clock(tmp_path / "cctv.mp4", **kw)
    with video.open("rb") as fh:
        v = client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("cctv.mp4", fh, "video/mp4"))])
    return case, v.json()[0]


def test_ocr_auto_calibration_with_clock_drift(client, tmp_path):
    case, video = _setup(client, tmp_path)
    frame = client.get(f"/api/videos/{video['id']}/frame", params={"t": 10})
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"

    test = client.post(f"/api/videos/{video['id']}/clock/ocr-test", json={"t": 10}).json()
    assert test["auto"] and test["roi"] and test["dvr"] == "2026-10-12T14:03:30"
    assert test["roi"][1] < 0.2  # zona no topo da imagem

    r = client.post(f"/api/videos/{video['id']}/clock/ocr", json={"samples": 10, "clock_offset_s": -180,
                                                                   "note": "DVR 3 min adiantado"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["time_source"] == "ocr" and abs(v["dvr_rate"] - 1.05) < 0.01
    start = datetime.fromisoformat(v["dvr_start"])
    assert abs((start - START).total_seconds()) <= 1.0
    cal = v["time_calibration"]
    assert cal["inliers"] >= 8 and cal["max_residual_s"] <= 1.5

    # A hora real aparece nos resultados exportados (relógio do DVR − 180 s)
    with (tmp_path / "ref.jpg").open("wb") as fh:
        img = np.zeros((200, 200, 3), np.uint8)
        cv2.rectangle(img, (20, 50), (80, 110), RED, -1)
        fh.write(cv2.imencode(".jpg", img)[1].tobytes())
    with (tmp_path / "ref.jpg").open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    from .conftest import wait_for_job

    job = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {
        "sample_fps": 1, "threshold": 0.05, "candidate_margin": 0, "motion_filter": False}}).json()
    job = wait_for_job(client, job["id"])
    data = client.get(f"/api/jobs/{job['id']}/export/json").json()
    first = data["detections"][0]
    expected = START + timedelta(seconds=first["timestamp_s"] * 1.05 - 180)
    got = datetime.fromisoformat(first["real_time"])
    assert abs((got - expected).total_seconds()) <= 1.5
    assert data["segments"][0]["real_start"]
    html = client.get(f"/api/jobs/{job['id']}/export/html").text
    assert "Hora real" in html and "OCR da data/hora na imagem" in html and "DVR 3 min adiantado" in html


def test_manual_clock_offset_and_reset(client, tmp_path):
    case, video = _setup(client, tmp_path, duration_s=10, rate=1.0)
    r = client.put(f"/api/videos/{video['id']}/clock",
                   json={"video_s": 4.0, "dvr_time": "2026-10-12T14:03:24", "clock_offset_s": 0})
    v = r.json()
    assert v["time_source"] == "manual" and v["dvr_start"].startswith("2026-10-12T14:03:20")
    v = client.patch(f"/api/videos/{video['id']}/clock/offset", json={"clock_offset_s": 30}).json()
    assert v["clock_offset_s"] == 30 and v["time_source"] == "manual"
    v = client.delete(f"/api/videos/{video['id']}/clock").json()
    assert v["dvr_start"] is None and v["time_source"] == ""
    actions = [e["action"] for e in client.get(f"/api/cases/{case['id']}/audit").json()]
    assert {"video.clock_set", "video.clock_offset", "video.clock_cleared"} <= set(actions)


def test_ocr_with_explicit_roi_and_us_format(client, tmp_path):
    _, video = _setup(client, tmp_path, duration_s=20, rate=1.0, fmt="%m/%d/%Y %I:%M:%S %p")
    r = client.post(f"/api/videos/{video['id']}/clock/ocr",
                    json={"roi": [0.0, 0.0, 0.7, 0.15], "samples": 5, "date_order": "MDY"})
    assert r.status_code == 200, r.text
    assert abs((datetime.fromisoformat(r.json()["dvr_start"]) - START).total_seconds()) <= 1.0


def test_ocr_fails_cleanly_without_timestamp(client, tmp_path):
    from .fakes import make_video

    case = client.post("/api/cases", json={"name": "Sem hora"}).json()
    with make_video(tmp_path / "v.mp4", [(0, 2, RED)], duration_s=3, fps=10).open("rb") as fh:
        video = client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("v.mp4", fh))]).json()[0]
    r = client.post(f"/api/videos/{video['id']}/clock/ocr", json={"samples": 3})
    assert r.status_code == 422 and "Marque a zona" in r.json()["detail"]
