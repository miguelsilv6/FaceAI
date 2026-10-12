"""Fluxo completo pela API com o motor falso: caso → referências → vídeo → análise → revisão → exportações."""

import csv
import io
import json
import zipfile

from .conftest import wait_for_job
from .fakes import BLUE, GREEN, RED, make_image, make_video


def _setup_case(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Caso Teste", "description": "CCTV loja"}).json()
    good = make_image(tmp_path / "suspeito.jpg", RED)
    many = make_image(tmp_path / "grupo.jpg", GREEN, size=(300, 200), n_faces=2)
    with good.open("rb") as a, many.open("rb") as b:
        r = client.post(f"/api/cases/{case['id']}/references",
                        files=[("files", ("suspeito.jpg", a, "image/jpeg")), ("files", ("grupo.jpg", b, "image/jpeg"))])
    assert r.status_code == 201, r.text
    refs = r.json()
    # Pessoa "vermelha" presente entre 3 s e 6 s; pessoa "azul" sempre presente.
    video = make_video(tmp_path / "cctv.mp4", [(3.0, 6.0, RED), (0.0, 10.0, BLUE)], duration_s=10, fps=10)
    with video.open("rb") as fh:
        r = client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("cctv.mp4", fh, "video/mp4"))])
    assert r.status_code == 201, r.text
    return case, refs, r.json()[0]


def test_full_flow(client, tmp_path):
    case, refs, video = _setup_case(client, tmp_path)
    assert refs[0]["valid"] and refs[0]["face_count"] == 1
    assert not refs[1]["valid"] and refs[1]["face_count"] == 2
    assert video["fps"] == 10 and video["frame_count"] == 100

    system = client.get("/api/system").json()
    assert "ArcFace" in [m["name"] for m in system["models"]]

    r = client.post(f"/api/cases/{case['id']}/jobs",
                    json={"params": {"sample_fps": 2, "threshold": 0.05, "candidate_margin": 0.0}})
    assert r.status_code == 201, r.text
    job = wait_for_job(client, r.json()["id"])
    assert job["status"] == "completed", job
    assert job["reference_ids"] == [refs[0]["id"]]  # a referência inválida não é usada
    assert job["frames_processed"] == 20
    assert job["environment"]["engine"] == "fake"

    dets = client.get(f"/api/jobs/{job['id']}/detections", params={"only_matches": True}).json()
    assert [d["timestamp_s"] for d in dets] == [3.0, 3.5, 4.0, 4.5, 5.0, 5.5]
    assert all(d["reference_id"] == refs[0]["id"] for d in dets)

    segs = client.get(f"/api/jobs/{job['id']}/segments").json()
    assert len(segs) == 1 and segs[0]["start_s"] == 3.0 and segs[0]["end_s"] == 5.5

    assert client.get(f"/api/detections/{dets[0]['id']}/crop").headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/videos/{video['id']}/stream", headers={"Range": "bytes=0-99"}).status_code == 206

    # Revisão humana: rejeitar a primeira deteção encurta o segmento.
    r = client.patch(f"/api/detections/{dets[0]['id']}/review", json={"status": "rejected", "note": "falso positivo"})
    assert r.json()["review_status"] == "rejected" and r.json()["reviewed_by"] == "Analista Teste"
    segs = client.get(f"/api/jobs/{job['id']}/segments").json()
    assert segs[0]["start_s"] == 3.5

    # Exportações
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/jobs/{job['id']}/export/csv").text.lstrip("﻿"))))
    assert len(rows) == 6 and rows[0]["review_status"] == "rejected" and rows[0]["timestamp"] == "00:00:03.000"
    data = client.get(f"/api/jobs/{job['id']}/export/json").json()
    assert data["case"]["name"] == "Caso Teste" and len(data["segments"]) == 1
    html = client.get(f"/api/jobs/{job['id']}/export/html").text
    assert "Segmentos de aparição" in html and "suspeito.jpg" in html and "falso positivo" in html
    pdf = client.get(f"/api/jobs/{job['id']}/export/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{job['id']}/export/zip").content))
    names = z.namelist()
    assert {"relatorio.html", "deteccoes.csv", "resultados.json", "auditoria.jsonl"} <= set(names)
    assert sum(n.startswith("crops/") for n in names) == 6

    # Auditoria
    actions = [e["action"] for e in client.get(f"/api/cases/{case['id']}/audit").json()]
    for expected in ("case.created", "reference.uploaded", "video.uploaded", "job.created", "job.started",
                     "job.completed", "detection.reviewed", "job.exported"):
        assert expected in actions
    audit_lines = [json.loads(line) for line in z.read("auditoria.jsonl").decode().splitlines()]
    assert audit_lines[-1]["action"] == "job.exported"


def test_candidates_are_kept_but_not_matches(client, tmp_path):
    case, _refs, _video = _setup_case(client, tmp_path)
    # Limiar 0 com uma margem enorme: as faces vermelhas são matches (distância 0), as azuis são só candidatos.
    r = client.post(f"/api/cases/{case['id']}/jobs",
                    json={"params": {"sample_fps": 1, "threshold": 0.001, "candidate_margin": 0.5}})
    job = wait_for_job(client, r.json()["id"])
    dets = client.get(f"/api/jobs/{job['id']}/detections").json()
    assert {d["is_match"] for d in dets} == {True, False}
    assert job["matches"] == sum(d["is_match"] for d in dets)


def test_validation_errors(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Vazio"}).json()
    assert client.post(f"/api/cases/{case['id']}/jobs", json={}).status_code == 422
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"MZ")
    with bad.open("rb") as fh:
        assert client.post(f"/api/cases/{case['id']}/references", files=[("files", ("x.exe", fh))]).status_code == 415
    assert client.get("/api/cases/999").status_code == 404
    assert client.post(f"/api/cases/{case['id']}/jobs", json={"params": {"model": "Nope"}}).status_code == 422
