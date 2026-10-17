"""Modo descoberta (sem amostra) e download do frame original/anotado."""

import csv
import hashlib
import io
import zipfile

import cv2
import numpy as np

from app.services.clustering import cluster_embeddings

from .conftest import wait_for_job
from .fakes import BLUE, GREEN, RED, make_video


def _case_with_video(client, tmp_path, timeline, duration_s=12):
    case = client.post("/api/cases", json={"name": "Descoberta"}).json()
    video = make_video(tmp_path / "cctv.mp4", timeline, duration_s=duration_s, fps=10)
    with video.open("rb") as fh:
        r = client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("cctv.mp4", fh, "video/mp4"))])
    assert r.status_code == 201, r.text
    return case, r.json()[0]


def _discover(client, case_id, **params):
    body = {"params": {"mode": "discover", "sample_fps": 2, "threshold": 0.05, "motion_filter": False,
                       "tracking": False, **params}}
    r = client.post(f"/api/cases/{case_id}/jobs", json=body)
    assert r.status_code == 201, r.text
    return wait_for_job(client, r.json()["id"])


def test_cluster_embeddings_groups_by_identity():
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=64), rng.normal(size=64)
    embs = np.stack([a + rng.normal(scale=0.05, size=64) for _ in range(3)]
                    + [b + rng.normal(scale=0.05, size=64) for _ in range(2)])
    labels = cluster_embeddings(embs, [1, 1, 1, 5, 5], threshold=0.2)
    assert list(labels) == [1, 1, 1, 0, 0]  # o grupo de maior peso é o 0
    assert len(cluster_embeddings(embs[:0], [], 0.2)) == 0


def test_discover_groups_all_faces_without_reference(client, tmp_path):
    # Vermelho aparece duas vezes (tracks separados: intervalo > 1 s); azul aparece uma vez. Sem imagem de referência.
    case, video = _case_with_video(client, tmp_path, [(1.0, 4.0, RED), (6.0, 8.0, BLUE), (10.0, 12.0, RED)],
                                   duration_s=13)
    job = _discover(client, case["id"], track_gap_s=1.0)
    assert job["status"] == "completed", job
    assert job["params"]["tracking"] is True  # obrigatório no modo descoberta
    assert job["reference_ids"] == [] and job["persons"] == 2 and job["tracks"] == 3
    assert "2 pessoa(s)" in job["message"]

    persons = client.get(f"/api/jobs/{job['id']}/persons").json()
    assert [p["n_tracks"] for p in persons] == [2, 1]  # a pessoa 1 é a que mais aparece (vermelho)
    red, blue = persons
    assert red["review_status"] == "pending" and red["video_ids"] == [video["id"]]

    dets = client.get(f"/api/jobs/{job['id']}/detections").json()
    assert dets and all(d["reference_id"] is None and not d["is_match"] for d in dets)
    assert all(d["distance"] < 0.05 for d in dets)  # distância ao centro do grupo

    segs = client.get(f"/api/jobs/{job['id']}/segments", params={"person_id": red["person_id"]}).json()
    assert [(s["start_s"], s["end_s"]) for s in segs] == [(1.0, 3.5), (10.0, 11.5)]
    assert {s["person_id"] for s in client.get(f"/api/jobs/{job['id']}/segments").json()} == {1, 2}

    # Revisão de uma pessoa inteira aplica-se aos tracks e às deteções.
    r = client.patch(f"/api/jobs/{job['id']}/persons/{blue['person_id']}/review", json={"status": "rejected"})
    assert r.status_code == 200 and r.json()["review_status"] == "rejected"
    blue_tracks = client.get(f"/api/jobs/{job['id']}/tracks", params={"person_id": blue["person_id"]}).json()
    assert all(t["review_status"] == "rejected" for t in blue_tracks)
    assert client.get(f"/api/jobs/{job['id']}/segments", params={"person_id": blue["person_id"]}).json() == []

    # Correções do agrupamento: mover um track para uma pessoa nova e juntar pessoas.
    red_tracks = client.get(f"/api/jobs/{job['id']}/tracks", params={"person_id": red["person_id"]}).json()
    moved = client.patch(f"/api/tracks/{red_tracks[1]['id']}/person", json={"person_id": None}).json()
    assert moved["person_id"] == 3
    assert client.get(f"/api/jobs/{job['id']}").json()["persons"] == 3
    assert client.patch(f"/api/tracks/{red_tracks[1]['id']}/person", json={"person_id": 99}).status_code == 404
    persons = client.post(f"/api/jobs/{job['id']}/persons/merge", json={"person_ids": [3, 1]}).json()
    assert [p["n_tracks"] for p in persons] == [2, 1]
    assert client.get(f"/api/jobs/{job['id']}").json()["persons"] == 2

    # Usar uma face descoberta como referência e procurar essa pessoa.
    red_det = next(d for d in dets if d["track_id"] == red_tracks[0]["id"])
    ref = client.post(f"/api/detections/{red_det['id']}/as-reference")
    assert ref.status_code == 201, ref.text
    assert ref.json()["valid"] and ref.json()["filename"].startswith(f"analise{job['id']}_pessoa1_")
    r = client.post(f"/api/cases/{case['id']}/jobs",
                    json={"params": {"sample_fps": 2, "threshold": 0.05, "motion_filter": False}})
    match_job = wait_for_job(client, r.json()["id"])
    assert match_job["status"] == "completed" and match_job["matches"] > 0

    # Exportações do modo descoberta
    html = client.get(f"/api/jobs/{job['id']}/export/html").text
    assert "Pessoas distintas" in html and "Pessoa #1" in html and "descoberta" in html
    assert "1. Processo" in html and "Registo de auditoria" in html
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/jobs/{job['id']}/export/csv").text.lstrip("﻿"))))
    assert {r["person_id"] for r in rows} == {"1", "2"}
    data = client.get(f"/api/jobs/{job['id']}/export/json").json()
    assert [p["person_id"] for p in data["persons"]] == [1, 2]
    assert data["persons"][0]["appearances"][0]["start_s"] == 1.0
    assert client.get(f"/api/jobs/{job['id']}/export/pdf").content.startswith(b"%PDF")
    assert "relatorio.html" in zipfile.ZipFile(
        io.BytesIO(client.get(f"/api/jobs/{job['id']}/export/zip").content)).namelist()
    actions = [e["action"] for e in client.get(f"/api/cases/{case['id']}/audit").json()]
    assert {"person.reviewed", "track.person_changed", "person.merged", "reference.from_detection"} <= set(actions)


def test_discover_with_motion_filter_and_cascade(client, tmp_path):
    """Com os filtros por omissão: frames parados prolongam o track da pessoa (sem distâncias por calcular)."""
    case, _ = _case_with_video(client, tmp_path, [(1.0, 9.0, RED)], duration_s=10)
    body = {"params": {"mode": "discover", "sample_fps": 2, "threshold": 0.05}}  # filtro de movimento + cascata
    job = wait_for_job(client, client.post(f"/api/cases/{case['id']}/jobs", json=body).json()["id"])
    assert job["status"] == "completed", job
    assert job["frames_skipped_motion"] > 0 and job["persons"] == 1
    (track,) = client.get(f"/api/jobs/{job['id']}/tracks").json()
    assert track["start_s"] == 1.0 and track["end_s"] == 8.5 and track["person_id"] == 1


def test_match_mode_still_requires_reference(client, tmp_path):
    case, _ = _case_with_video(client, tmp_path, [(1.0, 4.0, RED)])
    r = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {"sample_fps": 2}})
    assert r.status_code == 422 and "Descobrir" in r.text
    r = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {"mode": "outro"}})
    assert r.status_code == 422


def test_person_endpoints_reject_match_jobs(client, tmp_path):
    case, _ = _case_with_video(client, tmp_path, [(1.0, 4.0, RED)])
    job = _discover(client, case["id"])
    from .fakes import make_image
    img = make_image(tmp_path / "ref.jpg", GREEN)
    with img.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    r = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {"sample_fps": 2, "motion_filter": False}})
    match_job = wait_for_job(client, r.json()["id"])
    assert client.post(f"/api/jobs/{match_job['id']}/persons/merge",
                       json={"person_ids": [1, 2]}).status_code == 409
    assert client.patch(f"/api/jobs/{job['id']}/persons/42/review", json={"status": "confirmed"}).status_code == 404


def _sequential_frame(path, index):
    cap = cv2.VideoCapture(str(path))
    for _ in range(index):
        cap.grab()
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame


def test_frame_downloads(client, tmp_path):
    case, video = _case_with_video(client, tmp_path, [(1.0, 4.0, RED), (5.0, 8.0, BLUE)])
    job = _discover(client, case["id"])
    det = client.get(f"/api/jobs/{job['id']}/detections").json()[0]

    # Frame original (sem anotações), igual ao frame N lido sequencialmente, como na análise.
    r = client.get(f"/api/detections/{det['id']}/frame/original")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["x-faceai-extraction"] == "seek" and float(r.headers["x-faceai-crop-error"]) <= 4
    assert "content-disposition" not in r.headers
    png = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    stored = client.get(f"/api/videos/{video['id']}/stream").content
    assert stored  # o vídeo continua acessível
    expected = _sequential_frame(tmp_path / "cctv.mp4", det["frame_index"])
    assert np.array_equal(png, expected)

    # Download: nome descritivo, registo na auditoria com o SHA-256 do ficheiro entregue (2.º pedido usa a cache).
    r = client.get(f"/api/detections/{det['id']}/frame/original", params={"download": 1})
    assert r.headers["x-faceai-extraction"] == "seek (cache)"
    cd = r.headers["content-disposition"]
    assert f"analise{job['id']}_video{video['id']}_frame{det['frame_index']:07d}" in cd and cd.endswith('original.png"')
    r2 = client.get(f"/api/detections/{det['id']}/frame", params={"download": 1, "analyst": "Outro"})
    assert r2.headers["content-type"] == "image/jpeg" and r2.headers["content-disposition"].endswith('anotado.jpg"')
    events = [e for e in client.get(f"/api/cases/{case['id']}/audit").json()
              if e["action"] == "detection.frame_downloaded"]
    assert [e["details"]["kind"] for e in events] == ["original", "anotado"]
    assert events[0]["details"]["extraction"] == "seek (cache)" and events[0]["details"]["crop_error"] <= 4
    assert events[0]["details"]["sha256"] == hashlib.sha256(r.content).hexdigest()
    assert events[1]["details"]["sha256"] == hashlib.sha256(r2.content).hexdigest()
    assert events[0]["actor"] == "admin.teste"
    # Sem download, a imagem anotada é servida normalmente e não gera registo.
    assert "content-disposition" not in client.get(f"/api/detections/{det['id']}/frame").headers


def test_extract_frame_falls_back_when_seek_is_wrong(tmp_path, monkeypatch):
    from app.services import frames
    from app.services.imageio import write_jpeg
    from app.services.video_processor import crop_with_margin

    # Frames 10–14: quadrado vermelho parado em x=30; frame 15 (só ele): quadrado verde em x=150.
    path = make_video(tmp_path / "v.mp4", [(1.0, 1.5, RED), (1.5, 1.6, GREEN)], duration_s=3, fps=10)

    def crop_for(index, bbox):
        frame = _sequential_frame(path, index)
        crop = tmp_path / f"crop{index}.jpg"
        write_jpeg(crop, crop_with_margin(frame, *bbox))
        return frame, crop

    real_read_at = frames._read_at
    monkeypatch.setattr(frames, "_read_at", lambda cap, index: real_read_at(cap, index + 2))  # seek errado

    # O frame verde é o único vizinho que coincide com o recorte → aceite.
    bbox = (150, 80, 71, 71)
    expected, crop = crop_for(15, bbox)
    out = frames.extract_frame(path, 15, crop, bbox)
    assert out.method == "vizinho" and np.array_equal(out.image, expected)

    # Cena parada: o seek cai no frame 15 (sem o quadrado), e vários vizinhos (10–14) coincidem → leitura
    # sequencial (exata), sem adivinhar.
    bbox = (30, 80, 71, 71)
    expected, crop = crop_for(13, bbox)
    out = frames.extract_frame(path, 13, crop, bbox)
    assert out.method == "sequencial" and np.array_equal(out.image, expected)

    # Sem recorte guardado não há verificação possível.
    out = frames.extract_frame(path, 12, tmp_path / "nao_existe.jpg", bbox)
    assert out.method == "seek (sem verificação)" and out.crop_error is None
