"""Espaço em disco por caso, tarefas em curso e ZIP dos frames originais validados."""

from __future__ import annotations

import csv
import hashlib
import io
import time
import zipfile

from sqlmodel import Session

from .conftest import new_client
from .fakes import BLUE, RED
from .test_discover import _case_with_video, _discover

PW = "girassol-trovoada-marmelo-9"


def test_case_size_in_list(client, tmp_path):
    from app.services import disk

    case = client.post("/api/cases", json={"name": "Vazio"}).json()
    row = next(c for c in client.get("/api/cases").json() if c["id"] == case["id"])
    assert row["size_bytes"] == 0
    case2, video = _case_with_video(client, tmp_path, [(1.0, 3.0, RED)], duration_s=4)
    disk.forget(case2["id"])
    row = next(c for c in client.get("/api/cases").json() if c["id"] == case2["id"])
    assert row["size_bytes"] >= video["size_bytes"] > 0
    # Uploads por concluir (fora da pasta do caso) também contam
    up = client.post(f"/api/cases/{case['id']}/uploads",
                     json={"kind": "video", "filename": "x.mp4", "size_bytes": 1000}).json()
    client.put(f"/api/uploads/{up['id']}?offset=0", content=b"\0" * 400,
               headers={"Content-Type": "application/octet-stream"})
    assert disk.case_size(_session(), case["id"], fresh=True) == 400


def _session():
    import app.db

    return Session(app.db.get_engine())


def test_activity_lists_running_tasks_for_accessible_cases(client):
    from app.models import Job, JobStatus, Upload, Video

    case = client.post("/api/cases", json={"name": "Com tarefas"}).json()
    with _session() as s:
        s.add(Job(case_id=case["id"], status=JobStatus.running, progress=0.4, eta_s=120, created_by="admin.teste"))
        s.add(Job(case_id=case["id"], status=JobStatus.completed))
        s.add(Video(case_id=case["id"], filename="cam1.dav", stored_path="/x", size_bytes=1,
                    playback_status="running", playback_progress=0.5))
        s.commit()
    from app.config import settings

    part = settings.data_dir / "uploads" / "abc.part"
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"\0" * 10)
    with _session() as s:
        s.add(Upload(id="abc", case_id=case["id"], kind="video", filename="grande.mp4", size_bytes=100,
                     temp_path=str(part)))
        s.commit()
    act = client.get("/api/activity").json()
    assert [(j["case_name"], j["progress"], j["eta_s"]) for j in act["jobs"]] == [("Com tarefas", 0.4, 120)]
    assert [c["filename"] for c in act["conversions"]] == ["cam1.dav"]
    assert [(u["filename"], u["received_bytes"]) for u in act["uploads"]] == [("grande.mp4", 10)]
    # Outro analista sem acesso ao caso não vê nada; anónimo → 401
    other = new_client("outro.analista", PW)
    act = other.get("/api/activity").json()
    assert (act["jobs"], act["conversions"], act["uploads"]) == ([], [], [])
    assert set(act["disk"]) == {"level", "percent", "free"}  # analista: só o essencial
    from fastapi.testclient import TestClient

    import app.main

    assert TestClient(app.main.app).get("/api/activity").status_code == 401
    # Upload parado há mais de 2 min deixa de aparecer
    old = time.time() - 600
    import os

    os.utime(part, (old, old))
    assert client.get("/api/activity").json()["uploads"] == []


def test_validated_frames_zip(client, tmp_path):
    case, video = _case_with_video(client, tmp_path, [(1.0, 4.0, RED), (5.0, 8.0, BLUE)])
    job = _discover(client, case["id"], sample_fps=4)
    url = f"/api/jobs/{job['id']}/frames/validated.zip"
    r = client.get(url)
    assert r.status_code == 404 and "confirmadas" in r.json()["detail"]
    # Mais deteções a partir da primeira: outra no mesmo frame, uma noutro frame e uma que vai ser rejeitada
    from app.models import Detection

    with _session() as s:
        base = s.get(Detection, client.get(f"/api/jobs/{job['id']}/detections").json()[0]["id"])
        data = base.model_dump(exclude={"id"})
        s.add_all([Detection(**{**data, "x": data["x"] + 1}),
                   Detection(**{**data, "frame_index": 50, "timestamp_s": 5.0, "crop_path": ""}),
                   Detection(**{**data, "frame_index": 60, "timestamp_s": 6.0})])
        s.commit()
    dets = client.get(f"/api/jobs/{job['id']}/detections").json()
    assert len(dets) == 4
    dets.sort(key=lambda d: d["id"])
    chosen = dets[:3]
    for d in chosen:
        client.patch(f"/api/detections/{d['id']}/review", json={"status": "confirmed", "note": "ok"})
    client.patch(f"/api/detections/{dets[3]['id']}/review", json={"status": "rejected"})

    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "frames_validados" in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    pngs = [n for n in zf.namelist() if n.endswith(".png")]
    rows = list(csv.DictReader(io.StringIO(zf.read("manifesto.csv").decode("utf-8-sig"))))
    assert sorted(int(x["deteccao"]) for x in rows) == sorted(d["id"] for d in chosen)
    assert len(pngs) == 2  # frames 10 e 50 (o frame 10 tem duas deteções e aparece uma só vez)
    for row in rows:  # SHA-256 do manifesto corresponde ao ficheiro no ZIP
        assert hashlib.sha256(zf.read(row["ficheiro"])).hexdigest() == row["sha256"]
        assert row["revisto_por"] == "admin.teste" and row["video"] == "cctv.mp4" and row["nota"] == "ok"
        assert row["local"] == "Geral" and row["camara"] == "cctv.mp4"
    assert "Deteções: 3 · frames: 2 · frames guardados: 0 · falhas: 0" in zf.read("LEIAME.txt").decode()
    events = [e for e in client.get(f"/api/cases/{case['id']}/audit").json()
              if e["action"] == "frames.validated_exported"]
    assert sorted(events[-1]["details"]["detections"]) == sorted(d["id"] for d in chosen)

    assert client.get(f"/api/cases/{case['id']}/frames/validated").json() == {"detections": 3, "frames": 2,
                                                                                 "saved_frames": 0}
    # ZIP do caso inteiro: igual ao da única análise
    rc = client.get(f"/api/cases/{case['id']}/frames/validated.zip")
    assert rc.status_code == 200
    assert sorted(n for n in zipfile.ZipFile(io.BytesIO(rc.content)).namelist() if n.endswith(".png")) == sorted(pngs)

    # Separador "Resultados": lista das deteções confirmadas e ZIP só das selecionadas
    items = client.get(f"/api/cases/{case['id']}/frames/validated/items").json()
    assert sorted(i["detection_id"] for i in items) == sorted(d["id"] for d in chosen)
    assert all(i["location_name"] == "Geral" and i["camera_name"] == "cctv.mp4" and i["reviewed_by"] == "admin.teste"
               for i in items)
    assert all(i["person_label"].startswith("Pessoa #") for i in items)  # modo descoberta
    pick = chosen[2]["id"]  # deteção do frame 50, sozinha nesse frame
    rs = client.get(f"/api/cases/{case['id']}/frames/validated.zip", params={"detection_ids": f"{pick},999999"})
    zs = zipfile.ZipFile(io.BytesIO(rs.content))
    rows = list(csv.DictReader(io.StringIO(zs.read("manifesto.csv").decode("utf-8-sig"))))
    assert [int(r["deteccao"]) for r in rows] == [pick] and len([n for n in zs.namelist() if n.endswith(".png")]) == 1
    assert client.get(f"/api/cases/{case['id']}/frames/validated.zip",
                      params={"detection_ids": str(dets[3]["id"])}).status_code == 404  # rejeitada: fora
    assert client.get(f"/api/cases/{case['id']}/frames/validated.zip",
                      params={"detection_ids": "x"}).status_code == 422


def _fake_usage(monkeypatch, total_gb: float, free_gb: float):
    from collections import namedtuple

    from app.services import disk

    gb = 1024**3
    usage = namedtuple("usage", "total used free")(int(total_gb * gb), int((total_gb - free_gb) * gb),
                                                   int(free_gb * gb))
    monkeypatch.setattr(disk.shutil, "disk_usage", lambda _p: usage)


def test_disk_alert_levels_and_guards(client, monkeypatch):
    from app.services import disk

    _fake_usage(monkeypatch, 100, 50)
    assert disk.disk_status()["level"] == "ok"
    _fake_usage(monkeypatch, 100, 15)
    assert disk.disk_status()["level"] == "warning"
    _fake_usage(monkeypatch, 100, 5)
    st = disk.disk_status()
    assert st["level"] == "critical" and st["percent"] == 95.0
    _fake_usage(monkeypatch, 10000, 1)  # 99,99 % livre em percentagem não importa: menos de 2 GB livres
    assert disk.disk_status()["level"] == "critical"

    # Alerta visível na faixa e registado (uma vez) na auditoria do sistema
    _fake_usage(monkeypatch, 100, 15)
    assert client.get("/api/activity").json()["disk"]["level"] == "warning"
    client.get("/api/activity")
    alerts = [e for e in client.get("/api/audit/system").json() if e["action"] == "system.disk_alert"]
    assert [a["details"]["level"] for a in alerts] == ["warning"]

    # Upload maior do que o espaço livre (menos a reserva) é recusado; análise com disco crítico também
    case = client.post("/api/cases", json={"name": "Disco"}).json()
    r = client.post(f"/api/cases/{case['id']}/uploads",
                    json={"kind": "video", "filename": "grande.mp4", "size_bytes": 14 * 1024**3})
    assert r.status_code == 507 and "Espaço em disco insuficiente" in r.json()["detail"]
    assert client.post(f"/api/cases/{case['id']}/uploads",
                       json={"kind": "video", "filename": "ok.mp4", "size_bytes": 1024}).status_code == 201
    _fake_usage(monkeypatch, 100, 5)
    assert client.post(f"/api/cases/{case['id']}/jobs", json={"params": {}}).status_code == 507


def test_saved_frames_from_player(client, tmp_path):
    case, video = _case_with_video(client, tmp_path, [(1.0, 4.0, RED)])
    job = _discover(client, case["id"], sample_fps=2)
    url = f"/api/jobs/{job['id']}/saved-frames"
    # vídeo de outra análise/caso → 422
    assert client.post(url, json={"video_id": 999999, "t": 1.0}).status_code == 422
    r = client.post(url, json={"video_id": video["id"], "t": 2.04, "note": " porta "})
    assert r.status_code == 201, r.text
    sf = r.json()
    assert sf["frame_index"] == 20 and sf["timestamp_s"] == 2.0 and sf["note"] == "porta" and "stored_path" not in sf
    img = client.get(f"/api/saved-frames/{sf['id']}/image")
    assert img.headers["content-type"] == "image/png" and hashlib.sha256(img.content).hexdigest() == sf["sha256"]
    # aparece nos resultados (frames validados) e no ZIP
    items = client.get(f"/api/cases/{case['id']}/frames/validated/items").json()
    saved = [i for i in items if i["type"] == "saved_frame"]
    assert len(saved) == 1 and saved[0]["saved_frame_id"] == sf["id"] and saved[0]["review_note"] == "porta"
    assert client.get(f"/api/cases/{case['id']}/frames/validated").json()["saved_frames"] == 1
    rz = client.get(f"/api/cases/{case['id']}/frames/validated.zip", params={"frame_ids": str(sf["id"])})
    zf = zipfile.ZipFile(io.BytesIO(rz.content))
    rows = list(csv.DictReader(io.StringIO(zf.read("manifesto.csv").decode("utf-8-sig"))))
    assert len(rows) == 1 and rows[0]["tipo"] == "frame guardado" and rows[0]["sha256"] == sf["sha256"]
    assert hashlib.sha256(zf.read(rows[0]["ficheiro"])).hexdigest() == sf["sha256"]
    # ZIP do caso inteiro também o inclui (mesmo sem deteções confirmadas)
    assert client.get(f"/api/cases/{case['id']}/frames/validated.zip").status_code == 200
    # outro utilizador sem acesso → 404; retirar fica na auditoria
    other = new_client("zeca.frames", "girassol-trovoada-marmelo-9")
    assert other.get(f"/api/saved-frames/{sf['id']}/image").status_code == 404
    assert other.delete(f"/api/saved-frames/{sf['id']}").status_code == 404
    client.get(f"/api/saved-frames/{sf['id']}/image", params={"download": True})
    assert client.delete(f"/api/saved-frames/{sf['id']}").status_code == 204
    actions = [e["action"] for e in client.get(f"/api/cases/{case['id']}/audit").json()]
    assert {"frame.saved", "frame.saved_downloaded", "frame.saved_deleted"} <= set(actions)
    assert not [i for i in client.get(f"/api/cases/{case['id']}/frames/validated/items").json()
                if i["type"] == "saved_frame"]
    # eliminar a análise elimina os frames guardados
    sf2 = client.post(url, json={"video_id": video["id"], "t": 1.0}).json()
    r = client.request("DELETE", f"/api/jobs/{job['id']}", json={"confirm_job_id": job["id"], "reason": "teste"})
    assert r.status_code == 204
    assert client.get(f"/api/saved-frames/{sf2['id']}/image").status_code == 404
