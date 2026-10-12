from dataclasses import dataclass

from app.services.segments import build_segments


@dataclass
class D:
    id: int
    video_id: int
    timestamp_s: float
    distance: float


def test_groups_by_gap_and_video():
    dets = [D(1, 1, 0.0, 0.5), D(2, 1, 1.0, 0.3), D(3, 1, 5.0, 0.4), D(4, 2, 1.0, 0.6), D(5, 1, 2.5, 0.45)]
    segs = build_segments(dets, max_gap_s=2.0)
    assert [(s.video_id, s.start_s, s.end_s) for s in segs] == [(1, 0.0, 2.5), (1, 5.0, 5.0), (2, 1.0, 1.0)]
    assert segs[0].best_detection_id == 2
    assert segs[0].detection_ids == [1, 2, 5]
    assert segs[0].to_dict()["count"] == 3


def test_empty():
    assert build_segments([]) == []
