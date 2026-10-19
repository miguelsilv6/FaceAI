"""Frames anotados gerados a pedido (não gravados por deteção) e painel de armazenamento."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import cv2
import numpy as np
from sqlmodel import Session, select

from .conftest import new_client, wait_for_job
from .fakes import BLUE, RED, make_image, make_video

PW = "girassol-trovoada-marmelo-9"


def _job(client, tmp_path, timeline=None, mode="match"):
    cid = client.post("/api/cases", json={"name": "Armazenamento"}).json()["id"]
    path = make_video(tmp_path / "v.mp4", timeline or [(0.5, 3.5, RED)], duration_s=4, fps=10)
    with path.open("rb") as fh:
        client.post(f"/api/cases/{cid}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    params = {"sample_fps": 10, "threshold": 0.05, "motion_filter": False, "tracking": True}
    if mode == "discover":
        params["mode"] = "discover"
    else:
        with make_image(tmp_path / "ref.jpg", RED).open("rb") as fh:
            client.post(f"/api/cases/{cid}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    job = wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json={"params": params}).json()["id"])
    return cid, job


def _decode(body: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)


def test_frames_are_generated_on_demand(client, tmp_path):
    import app.db
    from app.models import Detection

    cid, job = _job(client, tmp_path, [(0.5, 3.5, RED), (0.5, 3.5, BLUE)], mode="discover")
    from app.services.jobs import job_dir

    with Session(app.db.get_engine()) as s:
        from app.models import Job

        folder = job_dir(s.get(Job, job["id"]))
        dets = list(s.exec(select(Detection).where(Detection.job_id == job["id"])))
    assert dets and all(d.frame_path == "" for d in dets)
    assert not (folder / "frames").exists() or not any((folder / "frames").iterdir())  # nada gravado na análise
    # duas faces no mesmo frame: o frame limpo fica em cache uma só vez
    same = [d for d in dets if d.frame_index == dets[0].frame_index]
    assert len(same) == 2
    images = [_decode(client.get(f"/api/detections/{d.id}/frame").content) for d in same]
    assert images[0].shape == (240, 320, 3) and not np.array_equal(images[0], images[1])  # caixas diferentes
    assert len(list((folder / "frames_cache").glob("*.jpg"))) == 1
    crop = _decode(client.get(f"/api/detections/{same[0].id}/crop").content)
    d = same[0]
    region = images[0][d.y + 4:d.y + d.h - 4, d.x + 4:d.x + d.w - 4].astype(int)
    assert np.abs(region.mean(axis=(0, 1)) - crop[crop.shape[0] // 2, crop.shape[1] // 2]).max() < 40
    thumb = _decode(client.get(f"/api/detections/{d.id}/frame", params={"w": 160}).content)
    assert thumb.shape[1] == 160
    down = client.get(f"/api/detections/{d.id}/frame", params={"download": "1"})
    assert down.status_code == 200 and "attachment" in down.headers["content-disposition"]


def test_report_and_zip_still_have_frames(client, tmp_path):
    _, job = _job(client, tmp_path)
    html = client.get(f"/api/jobs/{job['id']}/export/html").text
    assert "data:image/jpeg;base64," in html
    names = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{job['id']}/export/zip").content)).namelist()
    assert any(n.startswith("frames/") for n in names) and any(n.startswith("crops/") for n in names)


def test_old_stored_frames_still_served_and_can_be_pruned(client, tmp_path, capsys):
    import app.db
    from app.models import Detection
    from app.services import annotated

    cid, job = _job(client, tmp_path)
    with Session(app.db.get_engine()) as s:  # análise de uma versão anterior: frame anotado gravado
        det = s.exec(select(Detection).where(Detection.job_id == job["id"])).first()
        old = Path(det.crop_path).parent.parent / "frames" / "antigo.jpg"
        old.parent.mkdir(exist_ok=True)
        old.write_bytes(annotated.for_detection(s, det))
        det.frame_path = str(old)
        s.add(det)
        s.commit()
        det_id = det.id
    assert client.get(f"/api/detections/{det_id}/frame").content == old.read_bytes()
    info = client.get(f"/api/cases/{cid}/storage", params={"fresh": True}).json()
    frames = next(c for c in info["categories"] if c["key"] == "frames")
    assert frames["files"] == 1 and frames["regenerable"]
    from app import cli

    cli.main(["prune-frames", str(cid), "--dry-run"])
    assert "Seriam libertados" in capsys.readouterr().out and old.exists()
    cli.main(["prune-frames", str(cid)])
    assert not old.exists()
    assert client.get(f"/api/detections/{det_id}/frame").status_code == 200  # gerado a pedido


def test_storage_panel_and_free(client, tmp_path):
    import app.db
    from app.models import AuditEvent, Video
    from app.services.hashing import file_sha256

    cid, job = _job(client, tmp_path)
    det = client.get(f"/api/jobs/{job['id']}/detections").json()[0]
    client.get(f"/api/detections/{det['id']}/frame")  # enche a cache
    client.get(f"/api/detections/{det['id']}/frame/original")
    with Session(app.db.get_engine()) as s:  # uma cópia de reprodução existente
        video = s.exec(select(Video).where(Video.case_id == cid)).first()
        pb = Path(video.stored_path).with_name("x_playback.mp4")
        pb.write_bytes(b"0" * 5000)
        video.playback_path, video.playback_status = str(pb), "ready"
        s.add(video)
        s.commit()
        original = (Path(video.stored_path), video.sha256)
    info = client.get(f"/api/cases/{cid}/storage", params={"fresh": True}).json()
    cats = {c["key"]: c for c in info["categories"]}
    assert cats["originals"]["bytes"] > 0 and not cats["originals"]["regenerable"]
    assert cats["crops"]["files"] > 0 and cats["caches"]["files"] >= 2 and cats["playback"]["bytes"] == 5000
    assert info["regenerable_bytes"] >= 5000 + cats["caches"]["bytes"]
    r = client.post(f"/api/cases/{cid}/storage/free", json={"targets": ["playback", "caches"]})
    assert r.status_code == 200, r.text
    after = {c["key"]: c for c in r.json()["storage"]["categories"]}
    assert after["playback"]["bytes"] == 0 and after["caches"]["files"] == 0
    assert after["crops"]["files"] == cats["crops"]["files"]  # a evidência fica
    assert file_sha256(original[0]) == original[1]
    v = client.get(f"/api/cases/{cid}/videos").json()[0]
    assert v["playback_path"] is None and v["playback_status"] == ""
    with Session(app.db.get_engine()) as s:
        assert s.exec(select(AuditEvent).where(AuditEvent.action == "storage.freed")).first()
    assert client.post(f"/api/cases/{cid}/storage/free", json={"targets": ["originals"]}).status_code == 422
    assert client.get(f"/api/detections/{det['id']}/frame").status_code == 200  # volta a ser gerado


def test_storage_permissions(client, tmp_path):
    cid, _ = _job(client, tmp_path)
    ana = new_client("ana.s", PW)
    client.post(f"/api/cases/{cid}/members", json={"username": "ana.s", "role": "member"})
    assert ana.get(f"/api/cases/{cid}/storage").status_code == 200
    assert ana.post(f"/api/cases/{cid}/storage/free", json={"targets": ["caches"]}).status_code == 403
    assert ana.get("/api/system/storage").status_code == 403
    g = client.get("/api/system/storage").json()
    assert g["cases"][0]["case_id"] == cid and g["disk"]["total"] > 0 and g["system"]["database"] > 0
