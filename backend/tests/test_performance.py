"""Desempenho da API: lista de deteções incremental e leve, compressão, cache das imagens e miniaturas."""

from __future__ import annotations

import cv2
import numpy as np

from .conftest import wait_for_job
from .fakes import RED, make_image, make_video


def _job(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Desempenho"}).json()["id"]
    path = make_video(tmp_path / "v.mp4", [(0.5, 3.5, RED)], duration_s=4, fps=10)
    with path.open("rb") as fh:
        client.post(f"/api/cases/{cid}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    with make_image(tmp_path / "ref.jpg", RED).open("rb") as fh:
        client.post(f"/api/cases/{cid}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    r = client.post(f"/api/cases/{cid}/jobs", json={"params": {"sample_fps": 10, "threshold": 0.05,
                                                               "motion_filter": False, "tracking": True}})
    return wait_for_job(client, r.json()["id"])


def test_detection_list_is_slim_and_incremental(client, tmp_path):
    job = _job(client, tmp_path)
    dets = client.get(f"/api/jobs/{job['id']}/detections").json()
    assert len(dets) > 3
    assert "crop_path" not in dets[0] and "frame_path" not in dets[0]  # sem caminhos do servidor
    assert {"id", "video_id", "timestamp_s", "review_status", "colors", "track_id"} <= set(dets[0])
    assert dets[0]["review_status"] == "pending" and dets[0]["colors"] == {}
    ids = sorted(d["id"] for d in dets)
    newer = client.get(f"/api/jobs/{job['id']}/detections", params={"after_id": ids[1]}).json()
    assert [d["id"] for d in newer] == ids[2:]
    assert len(client.get(f"/api/jobs/{job['id']}/detections", params={"limit": 2}).json()) == 2
    # revisão em massa (track → todas as suas deteções)
    tid = client.get(f"/api/jobs/{job['id']}/tracks").json()[0]["id"]
    assert client.patch(f"/api/tracks/{tid}/review", json={"status": "confirmed"}).status_code == 200
    reviewed = client.get(f"/api/jobs/{job['id']}/detections").json()
    assert all(d["review_status"] == "confirmed" and d["reviewed_by"] for d in reviewed if d["track_id"])
    assert all(d["reviewed_at"] for d in reviewed if d["track_id"])


def test_gzip_cache_headers_and_thumbnails(client, tmp_path):
    job = _job(client, tmp_path)
    r = client.get(f"/api/jobs/{job['id']}/detections", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    det = r.json()[0]
    crop = client.get(f"/api/detections/{det['id']}/crop", headers={"Accept-Encoding": "gzip"})
    assert crop.status_code == 200 and "immutable" in crop.headers["cache-control"]
    assert crop.headers.get("content-encoding") is None  # imagens não são comprimidas outra vez
    full = cv2.imdecode(np.frombuffer(client.get(f"/api/detections/{det['id']}/frame").content, np.uint8), 1)
    thumb = client.get(f"/api/detections/{det['id']}/frame", params={"w": 160})
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    img = cv2.imdecode(np.frombuffer(thumb.content, np.uint8), 1)
    assert img.shape[1] == min(160, full.shape[1])
    # o recorte e o frame têm o mesmo nome de ficheiro: as miniaturas não se misturam
    crop_thumb = cv2.imdecode(np.frombuffer(client.get(f"/api/detections/{det['id']}/crop",
                                                       params={"w": 160}).content, np.uint8), 1)
    assert crop_thumb.shape != img.shape
    assert client.get(f"/api/detections/{det['id']}/frame", params={"w": 999}).status_code == 422
