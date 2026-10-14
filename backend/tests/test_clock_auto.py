"""Leitura automática da hora (OCR) na importação dos vídeos."""

from __future__ import annotations

from dataclasses import replace

from app.services import clock_auto, ocr

from .fakes import make_video


def _upload(client, case_id, tmp_path, name="cam.mp4"):
    path = make_video(tmp_path / name, [], duration_s=2, fps=5)
    with path.open("rb") as fh:
        return client.post(f"/api/cases/{case_id}/videos", files=[("files", (name, fh, "video/mp4"))]).json()[0]


def _calibration_ok(*_a, **_k):
    return {"roi": [0.0, 0.0, 0.3, 0.05], "date_order": "DMY", "samples": [], "dvr_start": "2026-10-01T10:00:00.000",
            "rate": 1.0, "max_residual_s": 0.2, "inliers": 10, "valid_readings": 10, "inferred_dates": 0}


def test_upload_queues_and_ocr_sets_clock(client, tmp_path, monkeypatch):
    monkeypatch.setattr(clock_auto, "settings", replace(clock_auto.settings, auto_clock_ocr=True))
    queued: list[int] = []
    monkeypatch.setattr(clock_auto, "_enqueue", queued.append)
    monkeypatch.setattr(ocr, "calibrate", _calibration_ok)
    case = client.post("/api/cases", json={"name": "OCR"}).json()
    v = _upload(client, case["id"], tmp_path)
    assert v["clock_auto_status"] == "pending" and queued == [v["id"]]

    clock_auto.process(v["id"])
    got = next(x for x in client.get(f"/api/cases/{case['id']}/videos").json() if x["id"] == v["id"])
    assert got["clock_auto_status"] == "done"
    assert got["dvr_start"].startswith("2026-10-01T10:00:00") and got["time_source"] == "ocr"
    audit = client.get(f"/api/cases/{case['id']}/audit").json()
    assert any(e["action"] == "video.clock_set" and e["actor"] == clock_auto.ACTOR for e in audit)


def test_ocr_failure_leaves_video_without_clock(client, tmp_path, monkeypatch):
    monkeypatch.setattr(clock_auto, "settings", replace(clock_auto.settings, auto_clock_ocr=True))
    monkeypatch.setattr(clock_auto, "_enqueue", lambda _id: None)

    def no_text(*_a, **_k):
        raise ValueError("Não foi encontrada nenhuma data/hora na imagem.")

    monkeypatch.setattr(ocr, "calibrate", no_text)
    case = client.post("/api/cases", json={"name": "OCR falha"}).json()
    v = _upload(client, case["id"], tmp_path)
    clock_auto.process(v["id"])
    got = client.get(f"/api/cases/{case['id']}/videos").json()[0]
    assert got["clock_auto_status"] == "failed" and "data/hora" in got["clock_auto_message"]
    assert got["dvr_start"] is None


def test_ocr_never_overrides_manual_clock(client, tmp_path, monkeypatch):
    monkeypatch.setattr(clock_auto, "settings", replace(clock_auto.settings, auto_clock_ocr=True))
    monkeypatch.setattr(clock_auto, "_enqueue", lambda _id: None)
    monkeypatch.setattr(ocr, "calibrate", _calibration_ok)
    case = client.post("/api/cases", json={"name": "Manual"}).json()
    v = _upload(client, case["id"], tmp_path)
    client.put(f"/api/videos/{v['id']}/clock", json={"video_s": 0, "dvr_time": "2026-05-05T08:00:00"})
    clock_auto.process(v["id"])
    got = client.get(f"/api/cases/{case['id']}/videos").json()[0]
    assert got["dvr_start"].startswith("2026-05-05T08:00:00") and got["time_source"] == "manual"


def test_disabled_by_default_in_tests(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Off"}).json()
    assert _upload(client, case["id"], tmp_path)["clock_auto_status"] == ""
