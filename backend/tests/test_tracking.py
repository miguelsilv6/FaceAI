import numpy as np

from app.services.tracking import FaceTracker, iou

A = np.array([1.0, 0.0, 0.0, 0.0])
B = np.array([0.0, 1.0, 0.0, 0.0])


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 5, 5)) == 0.0
    assert 0 < iou((0, 0, 10, 10), (5, 5, 10, 10)) < 1


def test_same_identity_keeps_track_while_moving():
    tr = FaceTracker(max_gap_s=2.0)
    ids = [tr.update(t, [(10 + 80 * i, 10, 40, 40)], [A + 0.05 * i]) for i, t in enumerate((0, 0.5, 1.0, 1.5))]
    assert len({i[0] for i in ids}) == 1  # mesmo track apesar de não haver sobreposição das caixas


def test_two_people_get_two_tracks_and_stay_separate():
    tr = FaceTracker(max_gap_s=2.0)
    first = tr.update(0.0, [(0, 0, 40, 40), (200, 0, 40, 40)], [A, B])
    second = tr.update(0.5, [(210, 0, 40, 40), (10, 0, 40, 40)], [B, A])
    assert first[0] != first[1]
    assert second == [first[1], first[0]]


def test_track_expires_after_gap_unless_kept_alive():
    tr = FaceTracker(max_gap_s=1.0)
    t1 = tr.update(0.0, [(0, 0, 40, 40)], [A])[0]
    assert tr.update(3.0, [(0, 0, 40, 40)], [A])[0] != t1  # expirou
    tr2 = FaceTracker(max_gap_s=1.0)
    t2 = tr2.update(0.0, [(0, 0, 40, 40)], [A])[0]
    tr2.keep_alive(2.5, [t2])  # frames estáticos saltados, com a face visível
    assert tr2.update(3.0, [(0, 0, 40, 40)], [A])[0] == t2


def test_keep_alive_does_not_revive_absent_faces():
    tr = FaceTracker(max_gap_s=1.0)
    ta = tr.update(0.0, [(0, 0, 40, 40)], [A])[0]
    tb = tr.update(0.5, [(200, 0, 40, 40)], [B])[0]  # A já não está visível neste frame
    tr.keep_alive(2.5, [tb])
    ids = tr.update(3.0, [(0, 0, 40, 40), (200, 0, 40, 40)], [A, B])
    assert ids[1] == tb and ids[0] != ta  # A reaparece depois do intervalo: track novo
