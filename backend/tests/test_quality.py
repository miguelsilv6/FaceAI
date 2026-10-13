import cv2
import numpy as np

from app.services.quality import face_quality, frontal_score, sharpness_score, size_score


def _textured(size=120, seed=0):
    rng = np.random.default_rng(seed)
    img = (rng.random((size, size, 3)) * 255).astype(np.uint8)
    return cv2.resize(cv2.resize(img, (30, 30)), (size, size), interpolation=cv2.INTER_NEAREST)


def test_sharp_image_scores_higher_than_blurred():
    sharp = _textured()
    blurred = cv2.GaussianBlur(sharp, (21, 21), 8)
    assert sharpness_score(sharp) > 0.8
    assert sharpness_score(blurred) < sharpness_score(sharp) / 2


def test_size_score_bounds():
    assert size_score(10, 10) == 0.0
    assert size_score(200, 150) == 1.0
    assert 0 < size_score(50, 60) < 1


def test_frontal_vs_profile():
    frontal = np.array([[30, 40], [70, 40], [50, 60], [35, 80], [65, 80]], np.float32)
    profile = np.array([[30, 40], [50, 40], [62, 60], [35, 80], [50, 80]], np.float32)
    assert frontal_score(frontal) == 1.0
    assert frontal_score(profile) < 0.3
    assert frontal_score(None) is None


def test_face_quality_combines_components():
    img = np.zeros((300, 300, 3), np.uint8)
    img[50:170, 50:170] = _textured()
    lm = np.array([[86, 98], [134, 98], [110, 122], [92, 146], [128, 146]], np.float32)
    good = face_quality(img, 50, 50, 120, 120, lm)
    tiny = face_quality(img, 50, 50, 18, 18, lm)
    assert good.score > 0.8 and tiny.score < good.score
    assert set(good.as_dict()) == {"quality", "sharpness", "size_score", "frontal"}
