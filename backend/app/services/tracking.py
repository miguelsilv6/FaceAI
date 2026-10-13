"""Tracking de faces entre frames amostrados (associação gulosa por IoU + semelhança de embedding).

Junta as deteções da mesma pessoa num "track". Isto reduz o trabalho de revisão (um track em vez de dezenas de
cartões) e permite decidir o match pela média dos melhores frames do track, mais robusta do que frames isolados.
Como a amostragem é esparsa (1–5 fps), a semelhança do embedding pesa mais do que a sobreposição das caixas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .matcher import l2_normalize


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ix = max(0, min(ax1 + aw, bx1 + bw) - max(ax1, bx1))
    iy = max(0, min(ay1 + ah, by1 + bh) - max(ay1, by1))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


@dataclass
class _Track:
    id: int
    bbox: tuple[int, int, int, int]
    embedding: np.ndarray
    last_seen_s: float
    hits: int = 1
    alive_until_s: float = field(default=0.0)


class FaceTracker:
    def __init__(self, max_gap_s: float = 2.0, sim_threshold: float = 0.45, iou_threshold: float = 0.3,
                 iou_min_sim: float = 0.2):
        self.max_gap_s = max_gap_s
        self.sim_threshold = sim_threshold
        self.iou_threshold = iou_threshold
        self.iou_min_sim = iou_min_sim
        self._tracks: list[_Track] = []
        self._next_id = 1

    def keep_alive(self, timestamp_s: float, track_ids: list[int]) -> None:
        """Frames estáticos saltados pelo filtro de movimento: a cena não mudou, por isso os tracks visíveis no
        último frame analisado continuam presentes. Os restantes não são prolongados."""
        visible = set(track_ids)
        for t in self._tracks:
            if t.id in visible:
                t.alive_until_s = timestamp_s

    def update(self, timestamp_s: float, boxes: list[tuple[int, int, int, int]],
               embeddings: list[np.ndarray]) -> list[int]:
        active = [t for t in self._tracks
                  if timestamp_s - max(t.last_seen_s, t.alive_until_s) <= self.max_gap_s]
        self._tracks = active
        assigned: list[int | None] = [None] * len(boxes)
        if boxes:
            embs = l2_normalize(np.stack(embeddings))
            pairs = []
            for ti, t in enumerate(active):
                sims = embs @ t.embedding
                for fi, box in enumerate(boxes):
                    sim, ov = float(sims[fi]), iou(t.bbox, box)
                    if sim >= self.sim_threshold or (ov >= self.iou_threshold and sim >= self.iou_min_sim):
                        pairs.append((sim + ov, ti, fi))
            used_t, used_f = set(), set()
            for _, ti, fi in sorted(pairs, reverse=True):
                if ti in used_t or fi in used_f:
                    continue
                used_t.add(ti)
                used_f.add(fi)
                t = active[ti]
                t.bbox, t.last_seen_s, t.hits = boxes[fi], timestamp_s, t.hits + 1
                t.embedding = l2_normalize(t.embedding * (t.hits - 1) + embs[fi])[0]
                assigned[fi] = t.id
            for fi in range(len(boxes)):
                if assigned[fi] is None:
                    t = _Track(id=self._next_id, bbox=boxes[fi], embedding=embs[fi], last_seen_s=timestamp_s)
                    self._next_id += 1
                    self._tracks.append(t)
                    assigned[fi] = t.id
        return [int(a) for a in assigned]  # type: ignore[arg-type]
