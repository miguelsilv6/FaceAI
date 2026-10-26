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
def test_known_dvr_mode_is_applied_automatically_and_manager_can_change_it(client, tmp_path):
    from sqlmodel import Session

    import app.db
    from app.models import Video
    from app.services import aspect

    cid = client.post("/api/cases", json={"name": "Lite"}).json()["id"]
    v = _upload(client, cid, _squeezed(tmp_path, "lite.mp4", (960, 1080), None))
    # sem SAR no ficheiro, mas 960×1080 é o modo 1080P Lite: aplica-se sozinho
    assert v["aspect"]["display"] == [1920, 1080] and v["aspect"]["source"].startswith("automática: modo de DVR")
    assert any(e["details"]["display"] == [1920, 1080] and e["actor"] == "sistema"
               for e in _events(client, cid, "video.aspect_set"))

    ana = new_client("ana.a", PW)
    client.post(f"/api/cases/{cid}/members", json={"username": "ana.a", "role": "member"})
    body = {"width": 1440, "height": 1080, "reason": "no leitor do fabricante é 4:3"}
    assert ana.post(f"/api/videos/{v['id']}/aspect", json=body).status_code == 403
    r = client.post(f"/api/videos/{v['id']}/aspect", json=body)
    assert r.status_code == 200, r.text
    a = r.json()["aspect"]
    assert a["display"] == [1440, 1080] and a["source"] == "manual"
    assert a["suggested"]["display"] == [1920, 1080]  # a automática continua disponível no menu
    assert a["history"][-1]["reason"] == "no leitor do fabricante é 4:3"
    assert any(e["details"].get("reason") == "no leitor do fabricante é 4:3"
               for e in _events(client, cid, "video.aspect_set"))
    # a escolha manual não é substituída pela automática (ex.: nova extração ou arranque)
    with Session(app.db.get_engine()) as s:
        aspect.detect(s, s.get(Video, v["id"]))
    assert client.get(f"/api/cases/{cid}/videos").json()[0]["aspect"]["display"] == [1440, 1080]
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
    assert job["environment"]["display"][str(v["id"])]["size"] == [320, 240]
    assert job["environment"]["display"][str(v["id"])]["reason"] == "meia largura"
    started = [e for e in client.get(f"/api/cases/{cid}/audit").json() if e["action"] == "job.started"]
    assert any(e["details"]["environment"].get("display") for e in started)  # fica na auditoria
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
    assert v["aspect"]["display"] == [1920, 1080] and v["aspect"]["checked_at"]
    # sugestão por confirmar da versão anterior: passa a ser aplicada
    with Session(app.db.get_engine()) as s:
        video = s.get(Video, v["id"])
        video.aspect = {"checked_at": "x", "suggested": {"display": [1920, 1080], "mode": "1080P Lite"}}
        s.add(video)
        s.commit()
    aspect.backfill()
    assert client.get(f"/api/cases/{cid}/videos").json()[0]["aspect"]["display"] == [1920, 1080]


def _set_aspect(vid, aspect_value):
    from sqlmodel import Session

    import app.db
    from app.models import Video

    with Session(app.db.get_engine()) as s:
        video = s.get(Video, vid)
        video.aspect = aspect_value
        s.add(video)
        s.commit()


@needs_ffmpeg
def test_ambiguous_mode_is_only_suggested_and_probe_failure_is_not_marked(client, tmp_path, monkeypatch):
    from app.services import aspect

    cid = client.post("/api/cases", json={"name": "D1"}).json()["id"]
    v = _upload(client, cid, _squeezed(tmp_path, "d1.mp4", (704, 576), None))
    assert not v["aspect"].get("display")  # D1 também é um tamanho de câmaras IP: só sugestão
    assert v["aspect"]["suggested"]["display"] == [768, 576]
    # "Automática" no menu: aplica a sugestão? Não: volta à regra automática (que aqui não aplica nada)
    r = client.post(f"/api/videos/{v['id']}/aspect", json={"width": 768, "height": 576, "reason": "é 4:3"})
    assert r.json()["aspect"]["display"] == [768, 576] and r.json()["aspect"]["source"] == "manual"
    r = client.post(f"/api/videos/{v['id']}/aspect", json={"reason": "voltar ao automático", "auto": True})
    a = r.json()["aspect"]
    assert r.status_code == 200 and a.get("source") != "manual" and not a.get("display")
    assert a["history"][-1].get("auto") is True

    # sem ffprobe (ou falha): não é dado como verificado e nada é aplicado
    monkeypatch.setattr(aspect, "probe_sar", lambda path: (False, None))
    v2 = _upload(client, cid, _squeezed(tmp_path, "lite.mp4", (960, 1080), None))
    assert not v2["aspect"].get("checked_at") and not v2["aspect"].get("display")


@needs_ffmpeg
def test_unchecked_video_is_checked_before_analysis_and_copy_invalidated(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Corrida"}).json()["id"]
    v = _upload(client, cid, _squeezed(tmp_path, "lite.mp4", (960, 1080), None))
    _set_aspect(v["id"], {})  # como logo a seguir a uma atualização, antes de o arranque o verificar
    params = {"mode": "discover", "sample_fps": 5, "motion_filter": False, "tracking": True}
    job = wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json={"params": params}).json()["id"])
    assert job["environment"]["display"][str(v["id"])]["size"] == [1920, 1080]

    # mudar a proporção apaga a cópia de reprodução (feita no tamanho antigo)
    from sqlmodel import Session

    import app.db
    from app.models import Video
    fake = tmp_path / "x_playback.mp4"
    fake.write_bytes(b"x")
    with Session(app.db.get_engine()) as s:
        video = s.get(Video, v["id"])
        video.playback_path, video.playback_status = str(fake), "ready"
        s.add(video)
        s.commit()
    r = client.post(f"/api/videos/{v['id']}/aspect", json={"width": 1440, "height": 1080, "reason": "4:3"})
    assert r.status_code == 200 and not fake.exists() and r.json()["playback_path"] is None
    assert r.json()["playback_status"] in ("not_needed", "on_demand", "unavailable")  # sem ffmpeg nos testes


def test_aspect_change_refused_while_extracting(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Extração"}).json()["id"]
    v = _upload(client, cid, make_video(tmp_path / "v.mp4", [(0.5, 1.5, RED)], duration_s=2, fps=10))
    from sqlmodel import Session

    import app.db
    from app.models import Video
    with Session(app.db.get_engine()) as s:
        video = s.get(Video, v["id"])
        video.extraction_status = "running"
        s.add(video)
        s.commit()
    r = client.post(f"/api/videos/{v['id']}/aspect", json={"width": 640, "height": 240, "reason": "teste"})
    assert r.status_code == 409
