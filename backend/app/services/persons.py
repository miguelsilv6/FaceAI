"""Pessoas distintas do modo descoberta: grupos de tracks com o mesmo `person_id`."""

from __future__ import annotations

from typing import Any

from ..models import ReviewStatus, Track
from .segments import Interval, merge_intervals


def review_summary(statuses: list[ReviewStatus]) -> str:
    """Estado de revisão de um grupo: "confirmed"/"rejected" só se todos os tracks tiverem esse estado."""
    values = {s.value for s in statuses}
    if len(values) == 1:
        return values.pop()
    return "pending" if values <= {"pending"} else "mixed"


def summarize_persons(tracks: list[Track]) -> list[dict[str, Any]]:
    """Resumo por pessoa, ordenado pelo número da pessoa (1 = a que aparece mais)."""
    groups: dict[int, list[Track]] = {}
    for t in tracks:
        if t.person_id is not None:
            groups.setdefault(t.person_id, []).append(t)
    persons = []
    for pid in sorted(groups):
        ts = sorted(groups[pid], key=lambda t: (t.video_id, t.start_s))
        best = max(ts, key=lambda t: (t.best_quality, t.n_faces))
        persons.append({
            "person_id": pid,
            "track_ids": [t.id for t in ts],
            "n_tracks": len(ts),
            "n_faces": sum(t.n_faces for t in ts),
            "n_saved": sum(t.n_saved for t in ts),
            "video_ids": sorted({t.video_id for t in ts}),
            "first": {"video_id": ts[0].video_id, "start_s": ts[0].start_s},
            "duration_s": round(sum(t.end_s - t.start_s for t in ts), 3),
            "best_detection_id": best.best_detection_id,
            "best_quality": best.best_quality,
            "max_distance": max(t.track_distance for t in ts),
            "review_status": review_summary([t.review_status for t in ts]),
        })
    return persons


def person_segments(tracks: list[Track], person_id: int | None, gap_s: float) -> list[dict[str, Any]]:
    """Intervalos de aparição (por vídeo) de uma pessoa, ou de cada pessoa se person_id for None; exclui os
    tracks rejeitados na revisão. `count` é o número de faces detetadas no intervalo."""
    by_person: dict[int, list[Interval]] = {}
    faces: dict[int, int] = {}
    for t in tracks:
        if t.person_id is None or (person_id is not None and t.person_id != person_id):
            continue
        if t.review_status == ReviewStatus.rejected:
            continue
        faces[t.best_detection_id or -t.id] = t.n_faces
        by_person.setdefault(t.person_id, []).append(
            Interval(t.video_id, t.start_s, t.end_s, t.track_distance, t.best_detection_id,
                     (t.best_detection_id,) if t.best_detection_id is not None else ()))
    segs = []
    for pid in sorted(by_person):
        for seg in merge_intervals(by_person[pid], max_gap_s=gap_s):
            d = seg.to_dict()
            d["person_id"] = pid
            d["count"] = sum(faces.get(i, 0) for i in seg.detection_ids)
            segs.append(d)
    return sorted(segs, key=lambda d: (d["video_id"], d["start_s"], d["person_id"]))
