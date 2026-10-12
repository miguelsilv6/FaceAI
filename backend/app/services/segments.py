"""Agrupa deteções em intervalos contínuos de aparição."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Protocol


class _Det(Protocol):
    id: int | None
    video_id: int
    timestamp_s: float
    distance: float


@dataclass
class Segment:
    video_id: int
    start_s: float
    end_s: float
    best_distance: float
    best_detection_id: int | None
    detection_ids: list[int] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_s"] = round(self.duration_s, 3)
        d["best_similarity"] = round(1.0 - self.best_distance, 4)
        d["count"] = len(self.detection_ids)
        return d


def build_segments(detections: Iterable[_Det], max_gap_s: float = 2.0) -> list[Segment]:
    """Junta deteções do mesmo vídeo separadas por no máximo `max_gap_s` segundos."""
    by_video: dict[int, list[_Det]] = {}
    for det in detections:
        by_video.setdefault(det.video_id, []).append(det)

    segments: list[Segment] = []
    for video_id in sorted(by_video):
        current: Segment | None = None
        for det in sorted(by_video[video_id], key=lambda d: d.timestamp_s):
            if current is not None and det.timestamp_s - current.end_s <= max_gap_s:
                current.end_s = det.timestamp_s
                current.detection_ids.append(det.id)
                if det.distance < current.best_distance:
                    current.best_distance = det.distance
                    current.best_detection_id = det.id
                continue
            if current is not None:
                segments.append(current)
            current = Segment(
                video_id=video_id,
                start_s=det.timestamp_s,
                end_s=det.timestamp_s,
                best_distance=det.distance,
                best_detection_id=det.id,
                detection_ids=[det.id],
            )
        if current is not None:
            segments.append(current)
    return segments
