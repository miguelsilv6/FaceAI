"""Benchmark do pipeline de análise com um vídeo sintético "tipo CCTV".

O vídeo tem fundo estático com textura, um relógio sobreposto que muda a cada segundo (como num DVR) e "pessoas"
(quadrados de cor, reconhecidos pelo motor de teste) a entrar e a sair. Mede o tempo total, frames/s, a taxa de
frames saltados pelo filtro de movimento e o tempo por fase (job.environment["timings"], quando existe), e guarda
um "resultado de referência" (correspondências, segmentos, tracks) para confirmar que uma otimização não muda os
resultados.

    cd backend && ../.venv/bin/python ../tools/bench_pipeline.py --out /tmp/bench.json [--compare antes.json]
        [--duration 120] [--mode match|discover] [--no-roi] [--onnx-detector]

--onnx-detector mede só o detetor SCRFD real (modelos instalados) em frames 1080p, para comparar opções do ONNX
Runtime; não precisa de faces.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

RED, BLUE, GREEN = (0, 0, 255), (255, 0, 0), (0, 255, 0)
W, H = 640, 360
CLOCK_ROI = [0.02, 0.02, 0.36, 0.09]  # x, y, w, h (frações), onde o relógio é desenhado
BIG_CLOCK_ROI = [0.02, 0.02, 0.5, 0.12]


def make_cctv_video(path: Path, duration_s: float, fps: int = 25, big_clock: bool = False) -> Path:
    rng = np.random.default_rng(7)
    background = rng.integers(0, 30, (H, W, 3), dtype=np.uint8)  # textura estática (abaixo do limiar do motor falso)
    timeline = [(10, 25, RED), (40, 55, BLUE), (70, 90, RED), (95, 110, GREEN)]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    for i in range(int(duration_s * fps)):
        t = i / fps
        frame = background.copy()
        sec = int(t)
        # relógio: cinzento escuro (o motor falso ignora < 40) mas muda a cada segundo, como num DVR
        text = f"2026-10-17 12:{sec // 60:02d}:{sec % 60:02d}"
        if big_clock:  # relógio grande e branco (pior caso para o filtro de movimento)
            cv2.putText(frame, text[-8:], (16, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3, cv2.LINE_AA)
        else:
            cv2.putText(frame, text, (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (38, 38, 38), 2, cv2.LINE_AA)
        for k, (start, end, color) in enumerate(timeline):
            if start <= t < end:
                x0 = 60 + int((t - start) * 12) + k * 30  # a pessoa desloca-se
                cv2.rectangle(frame, (x0, 150), (x0 + 70, 220), color, -1)
        writer.write(frame)
    writer.release()
    return path


def run(args) -> dict:
    d = tempfile.mkdtemp()
    os.environ.update(FACEAI_DATA_DIR=d + "/data", FACEAI_FRONTEND_DIST=d + "/nofront", FACEAI_PRELOAD_MODELS="0",
                      FACEAI_AUTO_CLOCK_OCR="0")
    from app.services import face_engine, transcode
    from tests.fakes import FakeEngine, make_image

    if args.engine == "onnx":
        from app.services.engine_onnx import OnnxEngine

        face_engine.set_engine(OnnxEngine())
    else:
        face_engine.set_engine(FakeEngine())
    if args.no_prefetch:
        import app.services.jobs as jobs_mod

        jobs_mod.DECODE_PREFETCH = 0
    transcode.ffmpeg_available = lambda: False
    logging.disable(logging.INFO)
    from fastapi.testclient import TestClient
    from sqlmodel import Session

    import app.db
    import app.main
    from app.models import Video
    from tests.conftest import create_user, login

    video = make_cctv_video(Path(d) / "cctv.mp4", args.duration, big_clock=args.big_clock)
    with TestClient(app.main.app) as c:
        create_user("bench.admin", "cavalo-bateria-agrafo-correto", role="admin")
        login(c, "bench.admin", "cavalo-bateria-agrafo-correto")
        cid = c.post("/api/cases", json={"name": "Bench"}).json()["id"]
        with video.open("rb") as fh:
            vid = c.post(f"/api/cases/{cid}/videos", files=[("files", ("cctv.mp4", fh, "video/mp4"))]).json()[0]["id"]
        if not args.no_roi:
            with Session(app.db.get_engine()) as s:
                v = s.get(Video, vid)
                v.time_calibration = {"roi": BIG_CLOCK_ROI if args.big_clock else CLOCK_ROI,
                                      "text": "2026-10-17 12:00:00"}
                s.add(v)
                s.commit()
        params = {"sample_fps": args.sample_fps, "tracking": True, "motion_filter": not args.no_motion,
                  "cascade": not args.no_cascade}
        if args.engine != "onnx":
            params["threshold"] = 0.05
        if args.mode == "discover":
            params["mode"] = "discover"
        else:
            with open(make_image(Path(d) / "ref.jpg", RED), "rb") as fh:
                c.post(f"/api/cases/{cid}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
        t0 = time.perf_counter()
        jid = c.post(f"/api/cases/{cid}/jobs", json={"params": params}).json()["id"]
        while (job := c.get(f"/api/jobs/{jid}").json())["status"] in ("queued", "running"):
            time.sleep(0.05)
        elapsed = time.perf_counter() - t0
        assert job["status"] == "completed", job["message"]
        dets = c.get(f"/api/jobs/{jid}/detections").json()
        segs = c.get(f"/api/jobs/{jid}/segments").json()
        tracks = c.get(f"/api/jobs/{jid}/tracks").json()
    reference = {
        "matches": job["matches"], "faces_detected": job["faces_detected"], "tracks": job["tracks"],
        "persons": job["persons"],
        "detections": sorted((x["frame_index"], x["x"], x["y"], x["w"], x["h"], round(x["distance"], 4),
                              x["is_match"]) for x in dets),
        "segments": [(s["video_id"], round(s["start_s"], 2), round(s["end_s"], 2)) for s in segs],
        "track_spans": sorted((round(t["start_s"], 2), round(t["end_s"], 2), t["is_match"]) for t in tracks),
    }
    return {
        "elapsed_s": round(elapsed, 3),
        "frames_sampled": job["frames_sampled"],
        "frames_per_s": round(job["frames_sampled"] / elapsed, 1),
        "skipped_motion": job["frames_skipped_motion"],
        "skipped_motion_pct": round(100 * job["frames_skipped_motion"] / max(1, job["frames_sampled"]), 1),
        "frames_processed": job["frames_processed"],
        "timings": (job.get("environment") or {}).get("timings"),
        "reference": reference,
    }


def onnx_detector_bench(n: int = 40) -> dict:
    from app.services.engine_onnx import OnnxEngine

    eng = OnnxEngine()
    rng = np.random.default_rng(1)
    frames = [rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8) for _ in range(4)]
    eng.count_faces(frames[0], detector="scrfd_10g")  # carrega o modelo
    out = {}
    for name in ("scrfd_10g", "scrfd_500m"):
        t = time.perf_counter()
        for i in range(n):
            eng.count_faces(frames[i % 4], detector=name)
        out[name + "_ms"] = round((time.perf_counter() - t) / n * 1000, 1)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path)
    p.add_argument("--compare", type=Path, help="resultado anterior: confirma que o resultado de referência é igual")
    p.add_argument("--duration", type=float, default=120)
    p.add_argument("--sample-fps", type=float, default=5)
    p.add_argument("--mode", choices=("match", "discover"), default="match")
    p.add_argument("--no-roi", action="store_true", help="sem a região do relógio conhecida (sem OCR)")
    p.add_argument("--onnx-detector", action="store_true")
    p.add_argument("--big-clock", action="store_true", help="relógio grande e branco (pior caso do filtro)")
    p.add_argument("--engine", choices=("fake", "onnx"), default="fake",
                   help="onnx: SCRFD/ArcFace reais (modelos instalados); usar com --mode discover")
    p.add_argument("--no-prefetch", action="store_true", help="descodificação na mesma thread (comparação)")
    p.add_argument("--no-motion", action="store_true")
    p.add_argument("--no-cascade", action="store_true")
    args = p.parse_args()
    result = onnx_detector_bench() if args.onnx_detector else run(args)
    printable = {k: v for k, v in result.items() if k != "reference"}
    print(json.dumps(printable, indent=2, ensure_ascii=False))
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))
    if args.compare and "reference" in result:
        before = json.loads(args.compare.read_text())["reference"]
        after = json.loads(json.dumps(result["reference"]))
        same = before == after
        print("resultado de referência:", "IGUAL" if same else "DIFERENTE")
        if not same:
            for k in before:
                if before[k] != after.get(k):
                    print(f"  {k}: antes={str(before[k])[:200]} depois={str(after.get(k))[:200]}")
            sys.exit(1)


if __name__ == "__main__":
    main()
