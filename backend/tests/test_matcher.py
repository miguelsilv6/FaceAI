import numpy as np
import pytest

from app.services.matcher import ReferenceSet, is_match, l2_normalize


def test_l2_normalize_handles_zero_vector():
    out = l2_normalize(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert np.allclose(out[0], [0.6, 0.8])
    assert np.allclose(out[1], [0.0, 0.0])


def test_best_match_picks_closest_reference():
    refs = ReferenceSet(np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]))
    res = refs.best_match(np.array([0.1, 0.9, 0.0]))
    assert res.reference_index == 1
    assert res.distance == pytest.approx(1 - 0.9 / np.linalg.norm([0.1, 0.9]), abs=1e-6)
    assert res.similarity == pytest.approx(1 - res.distance)


def test_identical_vector_has_zero_distance_and_scale_invariance():
    refs = ReferenceSet(np.array([[2.0, 2.0, 1.0]]))
    assert refs.best_match(np.array([4.0, 4.0, 2.0])).distance == pytest.approx(0.0, abs=1e-6)


def test_threshold_is_inclusive():
    assert is_match(0.68, 0.68)
    assert not is_match(0.6801, 0.68)


def test_empty_reference_set_rejected():
    with pytest.raises(ValueError):
        ReferenceSet(np.zeros((0, 3)))
