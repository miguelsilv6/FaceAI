import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services.engine_onnx import ARCFACE_DST, DETECTORS, RECOGNIZERS, _nms, _umeyama, align_face


def test_umeyama_recovers_similarity_transform():
    angle, scale, tx, ty = np.deg2rad(20), 1.7, 12.0, -5.0
    rot = scale * np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    src = ARCFACE_DST.astype(np.float64)
    dst = src @ rot.T + [tx, ty]
    m = _umeyama(src, dst)
    assert np.allclose(m[:, :2], rot, atol=1e-6) and np.allclose(m[:, 2], [tx, ty], atol=1e-6)


def test_align_face_maps_landmarks_to_template():
    img = np.zeros((400, 400, 3), np.uint8)
    lm = ARCFACE_DST * 2 + 100
    for x, y in lm.astype(int):
        cv2.circle(img, (int(x), int(y)), 3, (255, 255, 255), -1)
    out = align_face(img, lm)
    assert out.shape == (112, 112, 3)
    for x, y in ARCFACE_DST.astype(int):
        assert out[y, x].max() > 100


def test_nms_suppresses_overlaps():
    dets = np.array([[0, 0, 10, 10, 0.9], [1, 1, 11, 11, 0.8], [50, 50, 60, 60, 0.7]], np.float32)
    assert _nms(dets, 0.4) == [0, 2]


def test_model_hashes_are_pinned():
    for mf in (*DETECTORS.values(), *RECOGNIZERS.values()):
        assert len(mf.sha256) == 64 and mf.url.startswith("https://github.com/deepinsight/insightface/")


MODELS = os.environ.get("FACEAI_MODELS_DIR")


@pytest.mark.skipif(not MODELS or not (Path(MODELS) / "buffalo_sc" / "det_500m.onnx").exists(),
                    reason="modelos ONNX não disponíveis (FACEAI_MODELS_DIR)")
def test_real_models_detect_and_embed():
    from app.services.engine_onnx import OnnxEngine

    eng = OnnxEngine()
    assert eng.detect(np.zeros((300, 300, 3), np.uint8), model="arcface_mbf", detector="scrfd_500m") == []
    assert eng.count_faces(np.zeros((300, 300, 3), np.uint8), detector="scrfd_500m") == 0
