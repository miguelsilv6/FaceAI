import numpy as np

from app.services.motion import MotionGate


def _frame(square_at=None):
    f = np.full((240, 320, 3), 60, np.uint8)
    if square_at is not None:
        x, y = square_at
        f[y:y + 60, x:x + 60] = 220
    return f


def test_static_frames_are_skipped_and_keyframes_forced():
    gate = MotionGate(threshold=0.002, keyframe_interval_s=5.0)
    decisions = [gate.check(_frame(), t).reason for t in (0.0, 0.5, 1.0, 4.5, 5.0, 5.5)]
    assert decisions == ["first", "static", "static", "static", "keyframe", "static"]


def test_motion_is_detected():
    gate = MotionGate(threshold=0.002, keyframe_interval_s=0)
    assert gate.check(_frame(), 0.0).analyze
    d = gate.check(_frame((100, 100)), 0.5)
    assert d.analyze and d.reason == "motion" and d.ratio > 0.01
    assert not gate.check(_frame((100, 100)), 1.0).analyze  # parou
    assert gate.check(_frame((160, 100)), 1.5).analyze  # mexeu-se


def test_disabled_threshold_analyzes_everything():
    gate = MotionGate(threshold=0.0, keyframe_interval_s=0)
    assert all(gate.check(_frame(), t).analyze for t in (0, 1, 2))
