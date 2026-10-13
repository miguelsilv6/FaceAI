"""Teste end-to-end com os modelos reais do motor ativo (ONNX: SCRFD + ArcFace; ou DeepFace).

É lento e precisa de imagens reais. Define FACEAI_E2E_DATASET com uma pasta com o conjunto de
teste do DeepFace (tests/unit/dataset de https://github.com/serengil/deepface), onde img1, img2,
img4 e img5 são a mesma pessoa e img8 e img16 são outras pessoas.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import face_engine

from .conftest import wait_for_job

DATASET = os.environ.get("FACEAI_E2E_DATASET")
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not DATASET, reason="FACEAI_E2E_DATASET não definido")]


def _paste(frame: np.ndarray, img: np.ndarray, x: int, height: int) -> None:
    scale = height / img.shape[0]
    small = cv2.resize(img, (int(img.shape[1] * scale), height))
    y = (frame.shape[0] - height) // 2
    w = min(small.shape[1], frame.shape[1] - x)
    frame[y:y + height, x:x + w] = small[:, :w]


def _make_cctv(path: Path, ds: Path) -> None:
    """Vídeo 960x540 a 10 fps, 12 s: pessoa B sempre visível; pessoa A só entre 4 s e 8 s."""
    a = [cv2.imread(str(ds / n)) for n in ("img2.jpg", "img4.jpg")]
    b = cv2.imread(str(ds / "img8.jpg"))
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (960, 540))
    for i in range(120):
        t = i / 10
        frame = np.full((540, 960, 3), 60, np.uint8)
        _paste(frame, b, 20, 260)
        if 4.0 <= t < 8.0:
            _paste(frame, a[int(t) % 2], 520, 260)
        writer.write(frame)
    writer.release()


def test_real_models_find_person_only_in_expected_interval(client, tmp_path):
    face_engine.set_engine(face_engine.create_engine())
    ds = Path(DATASET)
    case = client.post("/api/cases", json={"name": "E2E"}).json()
    with (ds / "img1.jpg").open("rb") as r1, (ds / "img5.jpg").open("rb") as r2:
        refs = client.post(f"/api/cases/{case['id']}/references",
                           files=[("files", ("img1.jpg", r1)), ("files", ("img5.jpg", r2))]).json()
    assert all(r["valid"] for r in refs), refs
    video = tmp_path / "cctv.mp4"
    _make_cctv(video, ds)
    with video.open("rb") as fh:
        assert client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("cctv.mp4", fh))]).status_code == 201
    job_id = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {"sample_fps": 1}}).json()["id"]
    job = wait_for_job(client, job_id, timeout=900)
    assert job["status"] == "completed", job
    segs = client.get(f"/api/jobs/{job_id}/segments").json()
    assert len(segs) == 1, segs
    assert segs[0]["start_s"] == pytest.approx(4.0) and segs[0]["end_s"] == pytest.approx(7.0)
    matches = client.get(f"/api/jobs/{job_id}/detections", params={"only_matches": True}).json()
    assert all(d["x"] >= 480 for d in matches)  # nunca a pessoa B (lado esquerdo)
