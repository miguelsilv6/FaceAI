"""Melhoria de imagem só para visualização: determinística, limitada e auditada no download."""

from __future__ import annotations

import hashlib

import cv2
import numpy as np
import pytest

from app.services.enhance import Enhancement, apply

from .fakes import RED
from .test_discover import _case_with_video, _discover


def test_operations_are_deterministic_and_bounded():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)
    e = Enhancement.parse("denoise,clahe,sharpen", 4, 1.4)
    a, b = apply(img, e), apply(img, e)
    assert np.array_equal(a, b) and a.shape == (240, 320, 3)
    assert not np.array_equal(apply(img, Enhancement.parse("clahe", 1, 1.0)), img)
    big = np.zeros((3000, 100, 3), np.uint8)
    assert max(apply(big, Enhancement.parse("", 4, 1.0)).shape[:2]) <= 4096
    for bad in (("xpto", 1, 1.0), ("", 3, 1.0), ("", 1, 9.0)):
        with pytest.raises(ValueError):
            Enhancement.parse(*bad)
    assert Enhancement.parse("", 1, 1.0).is_identity


def test_enhanced_endpoint(client, tmp_path):
    case, _ = _case_with_video(client, tmp_path, [(1.0, 3.0, RED)], duration_s=4)
    job = _discover(client, case["id"])
    det = client.get(f"/api/jobs/{job['id']}/detections").json()[0]
    crop_before = open(det["crop_path"], "rb").read()

    r = client.get(f"/api/detections/{det['id']}/enhanced", params={"ops": "clahe,sharpen", "upscale": 2})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["x-faceai-enhancement"] == "clahe + sharpen + x2"
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    crop = cv2.imread(det["crop_path"])
    assert img.shape[:2] == (crop.shape[0] * 2, crop.shape[1] * 2)
    assert "content-disposition" not in r.headers  # visualização: sem registo
    r2 = client.get(f"/api/detections/{det['id']}/enhanced", params={"src": "original", "ops": "clahe"})
    assert r2.status_code == 200
    assert client.get(f"/api/detections/{det['id']}/enhanced", params={"upscale": 3}).status_code == 422

    d = client.get(f"/api/detections/{det['id']}/enhanced", params={"ops": "clahe", "download": 1})
    assert d.headers["content-disposition"].endswith('face_melhorada.png"')
    assert open(det["crop_path"], "rb").read() == crop_before  # o recorte original não muda
    ev = [e for e in client.get(f"/api/cases/{case['id']}/audit").json()
          if e["action"] == "detection.enhanced_downloaded"]
    assert ev[-1]["details"]["enhancement"]["clahe"] is True
    assert ev[-1]["details"]["sha256"] == hashlib.sha256(d.content).hexdigest()
