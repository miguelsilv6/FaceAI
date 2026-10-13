"""Agrupa deteções (e tracks) em intervalos contínuos de aparição."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Protocol


class _Det(Protocol):
    id: int | None
    video_id: int
    timestamp_s: float
    distance: float


@dataclass(frozen=True)
class Interval:
    video_id: int
    start_s: float
    end_s: float
    best_distance: float
    best_detection_id: int | None
    detection_ids: tuple[int, ...]
    evidence: tuple[str, ...] = ("face",)  # "face" | "vestuário"


@dataclass
class Segment:
    video_id: int
    start_s: float
    end_s: float
    best_distance: float
    best_detection_id: int | None
    detection_ids: list[int] = field(default_factory=list)
    evidence: set[str] = field(default_factory=lambda: {"face"})

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> dict:
        d = asdict(self)
        d["evidence"] = "+".join(e for e in ("face", "vestuário") if e in self.evidence)
        d["duration_s"] = round(self.duration_s, 3)
        d["best_similarity"] = round(1.0 - self.best_distance, 4)
        d["count"] = len(self.detection_ids)
        return d


def merge_intervals(intervals: Iterable[Interval], max_gap_s: float = 2.0) -> list[Segment]:
    """Junta intervalos do mesmo vídeo que se sobrepõem ou distam no máximo `max_gap_s` segundos."""
    by_video: dict[int, list[Interval]] = {}
    for iv in intervals:
        by_video.setdefault(iv.video_id, []).append(iv)

    segments: list[Segment] = []
    for video_id in sorted(by_video):
        current: Segment | None = None
        for iv in sorted(by_video[video_id], key=lambda i: (i.start_s, i.end_s)):
            if current is not None and iv.start_s - current.end_s <= max_gap_s:
                current.end_s = max(current.end_s, iv.end_s)
                current.detection_ids.extend(d for d in iv.detection_ids if d not in current.detection_ids)
                current.evidence.update(iv.evidence)
                if iv.best_distance < current.best_distance:
                    current.best_distance, current.best_detection_id = iv.best_distance, iv.best_detection_id
                continue
            if current is not None:
                segments.append(current)
            current = Segment(video_id=video_id, start_s=iv.start_s, end_s=iv.end_s,
                              best_distance=iv.best_distance, best_detection_id=iv.best_detection_id,
                              detection_ids=list(iv.detection_ids), evidence=set(iv.evidence))
        if current is not None:
            segments.append(current)
    for seg in segments:
        seg.detection_ids.sort()
    return segments


def build_segments(detections: Iterable[_Det], max_gap_s: float = 2.0) -> list[Segment]:
    """Junta deteções do mesmo vídeo separadas por no máximo `max_gap_s` segundos."""
    return merge_intervals(
        (Interval(d.video_id, d.timestamp_s, d.timestamp_s, d.distance, d.id, (d.id,)) for d in detections),
        max_gap_s,
    )
