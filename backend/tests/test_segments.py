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


def test_match_segments_with_tracks():
    from types import SimpleNamespace as NS

    from app.models import ReviewStatus
    from app.services.reports import match_segments

    def det(i, t, dist, match, track=None, review=ReviewStatus.pending):
        return NS(id=i, video_id=1, timestamp_s=t, distance=dist, is_match=match, track_id=track,
                  review_status=review)

    track = NS(id=7, video_id=1, start_s=10.0, end_s=40.0, is_match=True, review_status=ReviewStatus.pending)
    dets = [
        det(1, 10.0, 0.8, False, 7),  # frame fraco, mas o track tem correspondência
        det(2, 15.0, 0.3, True, 7),   # frames-chave espaçados (pessoa parada)
        det(3, 20.0, 0.35, True, 7),
        det(4, 60.0, 0.4, True),      # correspondência isolada, sem track
    ]
    segs = match_segments(dets, 2.0, {7: track})
    assert [(s["start_s"], s["end_s"], s["count"]) for s in segs] == [(10.0, 40.0, 3), (60.0, 60.0, 1)]
    assert segs[0]["best_detection_id"] == 2

    track.review_status = ReviewStatus.rejected  # track rejeitado: só ficam deteções individuais com match
    segs = match_segments(dets, 2.0, {7: track})
    assert [(s["start_s"], s["end_s"]) for s in segs] == [(15.0, 15.0), (20.0, 20.0), (60.0, 60.0)]
