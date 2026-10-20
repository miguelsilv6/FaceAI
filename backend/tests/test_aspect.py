"""Proporção de apresentação: vídeos com píxeis não quadrados (modos "Lite"/meia resolução dos DVR)."""

from __future__ import annotations

import shutil
import subprocess

import cv2
import numpy as np
import pytest

from .conftest import new_client, wait_for_job
from .fakes import RED, make_video

PW = "girassol-trovoada-marmelo-9"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requer ffmpeg")


def _squeezed(tmp_path, name: str, size: tuple[int, int], sar: str | None) -> object:
    """O vídeo de teste (quadrado vermelho 70×70 em 320×240) gravado com a largura reduzida a metade."""
    src = make_video(tmp_path / "src.mp4", [(0.5, 2.5, RED)], duration_s=3, fps=10)
    out = tmp_path / name
    vf = f"scale={size[0]}:{size[1]}" + (f",setsar={sar}" if sar else ",setsar=1")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src), "-vf", vf, "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def _upload(client, cid, path):
    with path.open("rb") as fh:
        return client.post(f"/api/cases/{cid}/videos", files=[("files", (path.name, fh, "video/mp4"))]).json()[0]


def _events(client, cid, action):
    return [e for e in client.get(f"/api/cases/{cid}/audit").json() if e["action"] == action]


def test_sizes():
    from app.services import aspect

    assert aspect.size_for_sar(960, 1080, 2, 1) == (1920, 1080)
    assert aspect.size_for_sar(704, 288, 6, 11) == (704, 528)  # 2CIF PAL: a altura é que aumenta
    assert aspect.size_for_ratio(704, 480, 4 / 3) == (704, 528)  # nenhum lado é reduzido
    img = np.zeros((1080, 960, 3), np.uint8)
    assert aspect.fit(img, (1920, 1080)).shape == (1080, 1920, 3)
    assert aspect.fit(img, None) is img


@needs_ffmpeg
def test_sar_in_file_is_applied_on_import(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "SAR"}).json()["id"]
    v = _upload(client, cid, _squeezed(tmp_path, "sar.mp4", (160, 240), "2"))
    assert (v["width"], v["height"]) == (160, 240)
    assert v["aspect"]["display"] == [320, 240] and "SAR 2:1" in v["aspect"]["source"]
    ev = _events(client, cid, "video.aspect_set")
    assert ev and ev[0]["details"]["display"] == [320, 240]


@needs_ffmpeg
def test_known_dvr_mode_is_suggested_and_confirmed_by_manager(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Lite"}).json()["id"]
    v = _upload(client, cid, _squeezed(tmp_path, "lite.mp4", (960, 1080), None))
    assert not v["aspect"].get("display")  # sem SAR no ficheiro: não se aplica sozinho
    assert v["aspect"]["suggested"]["display"] == [1920, 1080]

    ana = new_client("ana.a", PW)
    client.post(f"/api/cases/{cid}/members", json={"username": "ana.a", "role": "member"})
    body = {"width": 1920, "height": 1080, "reason": "DVR em modo 1080P Lite"}
    assert ana.post(f"/api/videos/{v['id']}/aspect", json=body).status_code == 403
    r = client.post(f"/api/videos/{v['id']}/aspect", json=body)
    assert r.status_code == 200, r.text
    a = r.json()["aspect"]
    assert a["display"] == [1920, 1080] and a["source"] == "manual" and "suggested" not in a
    assert a["history"][-1]["reason"] == "DVR em modo 1080P Lite"
    assert _events(client, cid, "video.aspect_set")[0]["details"]["reason"] == "DVR em modo 1080P Lite"
    # repor a gravada
    r = client.post(f"/api/videos/{v['id']}/aspect", json={"reason": "afinal está certo"})
    assert r.status_code == 200 and not r.json()["aspect"].get("display")
    assert client.post(f"/api/videos/{v['id']}/aspect", json={"width": 10, "height": 10, "reason": "xxx"}) \
        .status_code == 422


@needs_ffmpeg
def test_analysis_uses_display_size_and_keeps_it(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Análise"}).json()["id"]
    v = _upload(client, cid, _squeezed(tmp_path, "sq.mp4", (160, 240), None))
    params = {"mode": "discover", "sample_fps": 5, "motion_filter": False, "tracking": True}

    def run():
        return wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json={"params": params}).json()["id"])

    before = run()
    # sem correção, a "face" fica espremida (35×70) e não é reconhecida como face, como acontece às reais
    assert client.get(f"/api/jobs/{before['id']}/detections").json() == []

    assert client.post(f"/api/videos/{v['id']}/aspect",
                       json={"width": 320, "height": 240, "reason": "meia largura"}).status_code == 200
    job = run()
    assert job["environment"]["display"] == {str(v["id"]): [320, 240]}
    dets = client.get(f"/api/jobs/{job['id']}/detections").json()
    assert dets and all(abs(d["w"] - d["h"]) <= 6 for d in dets)  # o quadrado volta a ser quadrado
    d = dets[0]
    for url in (f"/api/detections/{d['id']}/frame", f"/api/detections/{d['id']}/frame/original"):
        r = client.get(url)
        assert r.status_code == 200, (url, r.text)
        img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        assert img.shape[:2] == (240, 320), url
    sf = client.post(f"/api/jobs/{job['id']}/saved-frames", json={"video_id": v["id"], "t": 1.0, "note": ""})
    assert sf.status_code == 201 and (sf.json()["width"], sf.json()["height"]) == (320, 240)

    # a análise anterior continua com o tamanho com que foi feita
    assert "display" not in before["environment"]
    sf = client.post(f"/api/jobs/{before['id']}/saved-frames", json={"video_id": v["id"], "t": 1.0, "note": ""})
    assert (sf.json()["width"], sf.json()["height"]) == (160, 240)
    html = client.get(f"/api/jobs/{job['id']}/export/html").text
    assert "analisado como 320×240" in html and "meia largura" in html


@needs_ffmpeg
def test_backfill_checks_videos_imported_before(client, tmp_path):
    from sqlmodel import Session

    import app.db
    from app.models import Video
    from app.services import aspect

    cid = client.post("/api/cases", json={"name": "Antigos"}).json()["id"]
    v = _upload(client, cid, _squeezed(tmp_path, "old.mp4", (960, 1080), None))
    with Session(app.db.get_engine()) as s:
        video = s.get(Video, v["id"])
        video.aspect = {}  # importado por uma versão anterior
        s.add(video)
        s.commit()
    aspect.backfill()
    v = client.get(f"/api/cases/{cid}/videos").json()[0]
    assert v["aspect"]["suggested"]["mode"].startswith("1080P Lite") and v["aspect"]["checked_at"]
