"""Análise complementar por vestuário (re-identificação pelo corpo).

Na mesma visita, a pessoa veste a mesma roupa em todas as câmaras do estabelecimento — também de costas, de lado
ou longe, quando a face não serve. Esta análise:

1. deteta as pessoas (corpo inteiro) em cada frame analisado e segue-as entre frames (tracks de corpo);
2. associa as faces da análise facial ao corpo a que pertencem;
3. no fim, usa os corpos ligados a faces **com correspondência** como amostras do vestuário e procura, nos vídeos
   da análise e dentro de uma janela temporal, outros tracks de corpo com o mesmo vestuário:
   - distância de cosseno entre embeddings OSNet-AIN (mediana das K melhores) ≤ limiar ReID, **e**
   - distância entre as assinaturas de cor (tronco/pernas) ≤ limiar de cor (veto explicável);
   - um corpo cuja face foi vista e é claramente de outra pessoa nunca conta (a face prevalece);
4. o limiar ReID é calibrado no próprio caso: corpos com faces claramente de outras pessoas são negativos
   conhecidos, e o limiar fica abaixo das suas distâncias (≤ 5 % de falsos positivos nesses negativos).

O resultado é sempre evidência **complementar e indicativa**: fica por rever e nunca é confirmado automaticamente.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from sqlmodel import Session

from ..models import Detection, Job, Track, Video
from .body_engine import DEFAULT_PERSON_DETECTOR, DEFAULT_REID_THRESHOLD, BodyEngine, PersonObservation
from .clock import real_time
from .clothing import ColorSignature, color_distance, color_signature, frame_gain, mean_signature
from .imageio import write_jpeg
from .quality import sharpness_score
from .tracking import FaceTracker
from .video_processor import SampledFrame, annotate, crop_with_margin, format_timestamp

if TYPE_CHECKING:  # pragma: no cover
    from .jobs import _TrackState

log = logging.getLogger(__name__)

TOP_K = 5  # amostras (melhor qualidade) guardadas por track de corpo para o embedding
MIN_BODY_QUALITY = 0.2  # recortes abaixo disto não entram no embedding (só no tracking)
SAVE_QUALITY_STEP = 0.05
SAVE_EVERY_S = 10.0
NEGATIVE_FACE_MARGIN = 0.15  # face a mais de limiar + margem → "claramente outra pessoa"
MIN_NEGATIVES_FOR_CALIBRATION = 5
BODY_COLOR = (200, 60, 160)  # roxo (BGR): caixas de corpo nos frames anotados


@dataclass(frozen=True)
class FaceLink:
    """Face da passagem facial neste frame, para associar ao corpo."""

    bbox: tuple[int, int, int, int]
    distance: float | None
    state: _TrackState | None  # track facial (o is_match final só se sabe no fim)


def body_quality(frame: np.ndarray, p: PersonObservation) -> float:
    """0–1: tamanho, nitidez, corpo inteiro (proporção) e não cortado pela borda do frame."""
    H = frame.shape[0]
    size = float(np.clip((p.h - 64) / (192 - 64), 0.0, 1.0))
    crop = frame[p.y:p.y + p.h, p.x:p.x + p.w]
    sharp = sharpness_score(crop) if crop.size else 0.0
    aspect = p.h / max(1, p.w)
    shape = 1.0 if 1.6 <= aspect <= 4.5 else 0.6
    border = 0.7 if (p.y <= 2 or p.y + p.h >= H - 2) else 1.0
    return float((max(size, 1e-3) * max(sharp, 1e-3) * shape * border) ** (1 / 3))


def link_faces(persons: list[PersonObservation], faces: list[FaceLink]) -> dict[int, int]:
    """face → pessoa: o centro da face fica no terço superior do corpo e a face cabe na largura do corpo.
    Com vários corpos possíveis, fica o mais pequeno (o que melhor enquadra a face)."""
    out: dict[int, int] = {}
    for fi, f in enumerate(faces):
        fx, fy, fw, fh = f.bbox
        cx, cy = fx + fw / 2, fy + fh / 2
        best = None
        for pi, p in enumerate(persons):
            if p.x <= cx <= p.x + p.w and p.y - fh * 0.5 <= cy <= p.y + p.h * 0.4 and fw <= p.w * 0.95:
                if best is None or p.w * p.h < persons[best].w * persons[best].h:
                    best = pi
        if best is not None:
            out[fi] = best
    return out


@dataclass
class _Obs:
    quality: float
    embedding: np.ndarray
    colors: ColorSignature
    timestamp_s: float


@dataclass
class _Saved:
    frame_index: int
    timestamp_s: float
    bbox: tuple[int, int, int, int]
    confidence: float
    quality: float
    crop_path: Path
    frame_path: Path
    embedding: np.ndarray
    colors: ColorSignature


@dataclass
class BodyTrackState:
    video: Video
    start_s: float
    end_s: float
    n_obs: int = 0
    samples: list[_Obs] = field(default_factory=list)  # as TOP_K de melhor qualidade
    saved: list[_Saved] = field(default_factory=list)
    best_saved_quality: float = -1.0
    last_saved_s: float = -1e9
    face_distances: list[float] = field(default_factory=list)
    face_states: list[Any] = field(default_factory=list)

    def embeddings(self) -> np.ndarray:
        return np.stack([o.embedding for o in self.samples]) if self.samples else np.zeros((0, 512), np.float32)

    def signature(self) -> ColorSignature | None:
        return mean_signature([o.colors for o in self.samples]) if self.samples else None


@dataclass
class ClothingParams:
    detector: str = DEFAULT_PERSON_DETECTOR
    reid_threshold: float = DEFAULT_REID_THRESHOLD
    color_threshold: float = 0.6
    window_h: float = 12.0
    min_body_height: int = 80
    face_threshold: float = 0.65

    @classmethod
    def from_params(cls, p: dict) -> ClothingParams:
        return cls(detector=p.get("person_detector") or DEFAULT_PERSON_DETECTOR,
                   reid_threshold=float(p.get("reid_threshold") or DEFAULT_REID_THRESHOLD),
                   color_threshold=float(p.get("color_threshold", 0.6)),
                   window_h=float(p.get("clothing_window_h", 12.0)),
                   min_body_height=int(p.get("min_body_height", 80)),
                   face_threshold=float(p["threshold"]))


class BodyCollector:
    """Recolhe os tracks de corpo de todos os vídeos da análise; a decisão é tomada em finalize()."""

    def __init__(self, engine: BodyEngine, params: ClothingParams, out_dir: Path):
        self.engine, self.params, self.out_dir = engine, params, out_dir
        self.states: list[BodyTrackState] = []
        self._video: Video | None = None
        self._tracker: FaceTracker | None = None
        self._by_tid: dict[int, BodyTrackState] = {}
        self._visible: list[int] = []
        self.frames = 0
        self.persons = 0

    def start_video(self, video: Video, track_gap_s: float) -> None:
        self._video = video
        # Embeddings de corpo de pessoas diferentes ficam mais próximos do que os faciais: exige-se mais semelhança.
        self._tracker = FaceTracker(max_gap_s=max(track_gap_s, 2.0), sim_threshold=0.85, iou_threshold=0.3,
                                    iou_min_sim=0.7)
        self._by_tid = {}
        self._visible = []

    def keep_alive(self, timestamp_s: float) -> None:
        if self._tracker is None:
            return
        self._tracker.keep_alive(timestamp_s, self._visible)
        for tid in self._visible:
            self._by_tid[tid].end_s = timestamp_s

    def process(self, frame: SampledFrame, faces: list[FaceLink]) -> None:
        assert self._tracker is not None and self._video is not None
        img = frame.image
        self.frames += 1
        persons = [p for p in self.engine.detect_persons(img, detector=self.params.detector)
                   if p.h >= self.params.min_body_height]
        self._visible = []
        if not persons:
            return
        self.persons += len(persons)
        crops = [img[p.y:p.y + p.h, p.x:p.x + p.w] for p in persons]
        embs = self.engine.embed(crops)
        tids = self._tracker.update(frame.timestamp_s, [(p.x, p.y, p.w, p.h) for p in persons], list(embs))
        self._visible = list(tids)
        links = link_faces(persons, faces)
        gain = frame_gain(img)
        for i, (p, crop, emb, tid) in enumerate(zip(persons, crops, embs, tids, strict=True)):
            st = self._by_tid.get(tid)
            if st is None:
                st = self._by_tid[tid] = BodyTrackState(video=self._video, start_s=frame.timestamp_s,
                                                        end_s=frame.timestamp_s)
                self.states.append(st)
            st.end_s = frame.timestamp_s
            st.n_obs += 1
            for fi, pi in links.items():
                if pi == i:
                    f = faces[fi]
                    if f.distance is not None:
                        st.face_distances.append(f.distance)
                    if f.state is not None and f.state not in st.face_states:
                        st.face_states.append(f.state)
            q = body_quality(img, p)
            if q < MIN_BODY_QUALITY:
                continue
            sig = color_signature(crop, gain)
            if len(st.samples) < TOP_K or q > st.samples[-1].quality:
                st.samples.append(_Obs(q, emb, sig, frame.timestamp_s))
                st.samples.sort(key=lambda o: -o.quality)
                del st.samples[TOP_K:]
            if (not st.saved or q >= st.best_saved_quality + SAVE_QUALITY_STEP
                    or frame.timestamp_s - st.last_saved_s >= SAVE_EVERY_S):
                st.saved.append(self._save(frame, i, p, q, emb, sig))
                st.best_saved_quality = max(st.best_saved_quality, q)
                st.last_saved_s = frame.timestamp_s

    def _save(self, frame: SampledFrame, k: int, p: PersonObservation, q: float, emb: np.ndarray,
              sig: ColorSignature) -> _Saved:
        stem = f"v{self._video.id:04d}_f{frame.index:07d}_b{k}"
        crop_path = self.out_dir / "body_crops" / f"{stem}.jpg"
        frame_path = self.out_dir / "body_frames" / f"{stem}.jpg"
        write_jpeg(crop_path, crop_with_margin(frame.image, p.x, p.y, p.w, p.h, margin=0.05))
        label = f"{format_timestamp(frame.timestamp_s)} corpo q={q:.2f}"
        write_jpeg(frame_path, annotate(frame.image, p.x, p.y, p.w, p.h, label, BODY_COLOR))
        return _Saved(frame.index, frame.timestamp_s, (p.x, p.y, p.w, p.h), p.confidence, q, crop_path,
                      frame_path, emb, sig)

    # ------------------------------------------------------------------------------------------- decisão
    def finalize(self, s: Session, job: Job) -> dict[str, Any]:
        prm = self.params
        states = [st for st in self.states if st.samples]
        positives, negatives, candidates = [], [], []
        for st in states:
            face_match = any(getattr(fs.row, "is_match", False) for fs in st.face_states if fs.row is not None)
            min_face = min(st.face_distances) if st.face_distances else None
            if face_match or (min_face is not None and min_face <= prm.face_threshold):
                positives.append(st)
            elif min_face is not None and min_face > prm.face_threshold + NEGATIVE_FACE_MARGIN:
                negatives.append(st)  # a face mostra que é outra pessoa
            else:
                candidates.append(st)
        summary: dict[str, Any] = {"body_tracks": len(self.states), "persons_detected": self.persons,
                                   "samples": len(positives), "known_negatives": len(negatives)}
        if not positives:
            summary["note"] = ("Sem amostras de vestuário: não houve correspondências faciais com o corpo visível "
                               "(a pessoa tem de ser reconhecida pela face pelo menos uma vez).")
            self._cleanup(set())
            return summary

        gallery = np.concatenate([st.embeddings() for st in positives])
        gallery_sig = mean_signature([o.colors for st in positives for o in st.samples])

        def reid_dist(embs: np.ndarray, exclude: np.ndarray | None = None) -> float:
            g = gallery if exclude is None else exclude
            d = 1.0 - embs @ g.T
            k = max(1, min(TOP_K, d.size))
            return float(np.median(np.sort(d.reshape(-1))[:k]))

        # Calibração no próprio caso
        neg_d = sorted(reid_dist(st.embeddings()) for st in negatives)
        threshold = prm.reid_threshold
        calib: dict[str, Any] = {"default_threshold": prm.reid_threshold, "negatives": len(neg_d)}
        if len(neg_d) >= MIN_NEGATIVES_FOR_CALIBRATION:
            p5 = float(np.percentile(neg_d, 5))
            calib["negatives_p5"] = round(p5, 4)
            threshold = min(threshold, p5)
        pos_d = []
        for st in positives:  # pares positivos entre tracks diferentes (deixa o próprio track de fora)
            others = [o for o in positives if o is not st]
            if others:
                pos_d.append(reid_dist(st.embeddings(), np.concatenate([o.embeddings() for o in others])))
        if pos_d:
            calib["positives_median"] = round(float(np.median(pos_d)), 4)
            calib["positives_max"] = round(float(np.max(pos_d)), 4)
        if neg_d:
            calib["negatives_min"] = round(float(neg_d[0]), 4)
        calib["threshold_used"] = round(threshold, 4)
        summary["calibration"] = calib

        # Janela temporal (hora real do DVR)
        sample_times = [t for st in positives for t in (_real(st.video, st.start_s), _real(st.video, st.end_s)) if t]
        window_s = prm.window_h * 3600

        matched: list[tuple[BodyTrackState, float, float, str]] = []
        excluded_window = 0
        for st in candidates:
            note = ""
            t0, t1 = _real(st.video, st.start_s), _real(st.video, st.end_s)
            if prm.window_h > 0 and sample_times and t0 and t1:
                gap = min(min(abs((t0 - t).total_seconds()), abs((t1 - t).total_seconds())) for t in sample_times)
                if gap > window_s:
                    excluded_window += 1
                    continue
            elif prm.window_h > 0:
                note = "janela temporal não verificada (vídeo ou amostras sem hora real acertada)"
            rd = reid_dist(st.embeddings())
            sig = st.signature()
            cd = color_distance(sig, gallery_sig) if sig else 1.0
            if rd <= threshold and cd <= prm.color_threshold:
                matched.append((st, rd, cd, note))
        summary["excluded_by_window"] = excluded_window

        # Duas pessoas com a mesma roupa ao mesmo tempo no mesmo vídeo (fardas): marcar ambas como ambíguas.
        ambiguous: set[int] = set()
        for i, (a, *_) in enumerate(matched):
            for j in range(i + 1, len(matched)):
                b = matched[j][0]
                if a.video.id == b.video.id and a.start_s <= b.end_s and b.start_s <= a.end_s:
                    ambiguous.update((i, j))
        for i in ambiguous:
            st, rd, cd, note = matched[i]
            extra = "ambíguo: outra pessoa com roupa semelhante presente ao mesmo tempo (farda/uniforme?)"
            matched[i] = (st, rd, cd, f"{note}; {extra}" if note else extra)

        keep: set[Path] = set()
        for st in positives:
            others = [o for o in positives if o is not st]
            g = np.concatenate([o.embeddings() for o in others]) if others else None
            rd = reid_dist(st.embeddings(), g) if g is not None else 0.0
            self._persist(s, job, st, "face+vestuário", rd, color_distance(st.signature(), gallery_sig),
                          "amostra (corpo ligado a uma face com correspondência)", g, keep)
        for st, rd, cd, note in matched:
            self._persist(s, job, st, "vestuário", rd, cd, note, gallery, keep)
        self._cleanup(keep)
        summary["clothing_matches"] = len(matched)
        summary["gallery_colors"] = gallery_sig.names
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(self.out_dir / "body_embeddings.npz", gallery=gallery.astype(np.float32))
        except OSError:
            log.warning("Não foi possível gravar os embeddings de corpo do job %s", job.id)
        return summary

    def _persist(self, s: Session, job: Job, st: BodyTrackState, evidence: str, rd: float, cd: float, note: str,
                 gallery: np.ndarray | None, keep: set[Path]) -> None:
        sig = st.signature()
        row = Track(job_id=job.id, video_id=st.video.id, kind="body", evidence=evidence,
                    start_s=round(st.start_s, 3), end_s=round(st.end_s, 3), n_faces=st.n_obs,
                    n_saved=len(st.saved), best_distance=round(rd, 4), track_distance=round(rd, 4),
                    reid_distance=round(rd, 4), color_distance=round(cd, 4),
                    best_quality=round(max(o.quality for o in st.samples), 3), is_match=True,
                    colors=sig.names if sig else {}, note=note,
                    face_track_id=next((fs.row.id for fs in st.face_states if fs.row is not None), None))
        s.add(row)
        s.flush()
        best = None
        for sv in st.saved:
            d = float(np.min(1.0 - gallery @ sv.embedding)) if gallery is not None and len(gallery) else 0.0
            det = Detection(job_id=job.id, video_id=st.video.id, reference_id=None, kind="body",
                            frame_index=sv.frame_index, timestamp_s=round(sv.timestamp_s, 3),
                            x=sv.bbox[0], y=sv.bbox[1], w=sv.bbox[2], h=sv.bbox[3],
                            face_confidence=round(sv.confidence, 4), distance=round(d, 4),
                            similarity=round(1 - d, 4), is_match=True, crop_path=str(sv.crop_path),
                            frame_path=str(sv.frame_path), track_id=row.id, quality=round(sv.quality, 3),
                            colors=sv.colors.names)
            s.add(det)
            keep.update((sv.crop_path, sv.frame_path))
            if best is None or sv.quality > best[0]:
                best = (sv.quality, det)
        s.flush()
        if best is not None:
            row.best_detection_id = best[1].id
        s.add(row)

    def _cleanup(self, keep: set[Path]) -> None:
        for st in self.states:
            for sv in st.saved:
                for p in (sv.crop_path, sv.frame_path):
                    if p not in keep:
                        p.unlink(missing_ok=True)


def _real(video: Video, t: float) -> datetime | None:
    if not video.dvr_start:
        return None
    return real_time(video.dvr_start, video.dvr_rate, video.clock_offset_s, t)
