"""Otimizações do pipeline de análise: os resultados têm de ser exatamente os mesmos."""

from __future__ import annotations

import threading

import cv2
import numpy as np
import pytest

from app.services.imageio import AsyncJpegWriter, write_jpeg
from app.services.motion import MotionGate
from app.services.video_processor import sample_frames

from .conftest import wait_for_job
from .fakes import BLUE, RED, FakeEngine, make_image, make_video


def _frame_with_clock(sec: int) -> np.ndarray:
    f = np.full((360, 640, 3), 20, np.uint8)
    cv2.putText(f, f"12:00:{sec:02d}", (16, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3, cv2.LINE_AA)
    return f


def test_motion_mask_ignores_dvr_clock():
    plain = MotionGate(0.002, 0)
    masked = MotionGate(0.002, 0, mask_roi=[0.02, 0.02, 0.5, 0.12])
    for g in (plain, masked):
        assert g.check(_frame_with_clock(0), 0.0).analyze  # primeiro frame
    # só o relógio muda: sem máscara conta como movimento, com máscara não
    assert plain.check(_frame_with_clock(1), 1.0).analyze
    assert not masked.check(_frame_with_clock(2), 2.0).analyze
    # movimento real fora do relógio continua a ser detetado
    moving = _frame_with_clock(3)
    cv2.rectangle(moving, (300, 150), (380, 230), (0, 0, 255), -1)
    assert masked.check(moving, 3.0).analyze


def test_prefetch_gives_the_same_frames_and_stops_cleanly(tmp_path):
    path = make_video(tmp_path / "v.mp4", [(0.5, 2.5, RED), (1.0, 3.0, BLUE)], duration_s=4, fps=10)
    plain = list(sample_frames(path, sample_fps=5))
    pre = list(sample_frames(path, sample_fps=5, prefetch=3))
    assert [(f.index, f.timestamp_s) for f in plain] == [(f.index, f.timestamp_s) for f in pre]
    assert all(np.array_equal(a.image, b.image) for a, b in zip(plain, pre, strict=True))
    # o consumidor pára a meio (ex.: cancelamento): a thread de descodificação termina
    it = sample_frames(path, sample_fps=10, prefetch=2)
    next(it)
    it.close()
    assert not any(t.name == "faceai-decode" and t.is_alive() for t in threading.enumerate())


def test_async_writer_writes_identical_files_and_reports_errors(tmp_path):
    img = np.random.default_rng(3).integers(0, 255, (120, 160, 3), dtype=np.uint8)
    write_jpeg(tmp_path / "sync.jpg", img)
    w = AsyncJpegWriter(workers=2, max_pending=2)
    for i in range(5):
        w.submit(tmp_path / f"a{i}.jpg", img)
    w.flush()
    assert all((tmp_path / f"a{i}.jpg").read_bytes() == (tmp_path / "sync.jpg").read_bytes() for i in range(5))
    (tmp_path / "file").write_text("x")
    w.submit(tmp_path / "file" / "sub" / "x.jpg", img)  # pasta impossível: erro só aparece no flush
    with pytest.raises(OSError):
        w.flush()
    w.close()


def test_prefilter_skips_embedding_but_not_results(client, tmp_path, monkeypatch):
    import app.services.jobs as jobs

    calls = []
    real = FakeEngine.detect

    def spy(self, image_bgr, **kw):
        out = real(self, image_bgr, **kw)
        if kw.get("prefilter") is not None:  # só as chamadas da análise (não as das referências)
            calls.append((len(self._find(image_bgr)), len(out)))
        return out

    monkeypatch.setattr(FakeEngine, "detect", spy)
    cid = client.post("/api/cases", json={"name": "Pré-filtro"}).json()["id"]
    path = make_video(tmp_path / "v.mp4", [(0.5, 2.5, RED)], duration_s=3, fps=10)
    with path.open("rb") as fh:
        client.post(f"/api/cases/{cid}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    with open(make_image(tmp_path / "r.jpg", RED), "rb") as fh:
        client.post(f"/api/cases/{cid}/references", files=[("files", ("r.jpg", fh, "image/jpeg"))])
    base = {"sample_fps": 10, "threshold": 0.05, "motion_filter": False, "tracking": True}
    small = wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json={"params": base}).json()["id"])
    # o filtro aplicado no motor é o mesmo que a análise aplicava depois: as faces devolvidas são as contadas
    assert small["matches"] > 0 and small["faces_detected"] == sum(k for _, k in calls)
    calls.clear()
    big = wait_for_job(client, client.post(f"/api/cases/{cid}/jobs",
                                           json={"params": {**base, "min_face_size": 500}}).json()["id"])
    # faces abaixo do mínimo: o motor recebe o filtro e não devolve (nem calcula o embedding de) nenhuma
    assert big["status"] == "completed" and big["faces_detected"] == 0 and big["matches"] == 0
    assert calls and all(k == 0 for _, k in calls) and any(n > 0 for n, _ in calls)
    timings = small["environment"]["timings"]
    assert timings["total_s"] > 0 and "detect" in timings and "decode" in timings
    assert jobs.COMMIT_EVERY_S > 0
