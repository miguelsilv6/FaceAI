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
    assert [m["name"] for m in system["models"]] == ["fake_model"]
    assert system["defaults"]["fast_detector"] == "fake_fast" and system["upload_chunk_bytes"] > 0

    r = client.post(f"/api/cases/{case['id']}/jobs",
                    json={"params": {"sample_fps": 2, "threshold": 0.05, "candidate_margin": 0.0,
                                     "motion_filter": False}})
    assert r.status_code == 201, r.text
    job = wait_for_job(client, r.json()["id"])
    assert job["status"] == "completed", job
    assert job["reference_ids"] == [refs[0]["id"]]  # a referência inválida não é usada
    assert job["frames_sampled"] == 20
    assert job["environment"]["engine"] == "fake"
    assert job["params"]["model"] == "fake_model" and job["params"]["detector"] == "fake_det"

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


def test_motion_cascade_and_tracking(client, tmp_path):
    """Cena estática com a pessoa 'vermelha' parada entre 3 e 6 s: o filtro de movimento salta os frames
    parados, a cascata salta os frames sem faces, e o track cobre todo o tempo em que a pessoa está presente."""
    case = client.post("/api/cases", json={"name": "Estático"}).json()
    with make_image(tmp_path / "ref.jpg", RED).open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    video = make_video(tmp_path / "v.mp4", [(3.0, 6.0, RED)], duration_s=10, fps=10)
    with video.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    r = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {
        "sample_fps": 2, "threshold": 0.05, "candidate_margin": 0.0, "keyframe_interval_s": 0}})
    job = wait_for_job(client, r.json()["id"])
    assert job["status"] == "completed", job
    assert job["frames_sampled"] == 20
    # analisados: 0 s (1.º frame), 3 s (entrada) e 6 s (saída); o resto é estático
    assert job["frames_skipped_motion"] == 17
    assert job["frames_skipped_noface"] == 2   # 0 s e 6 s não têm faces na 1.ª passagem
    assert job["frames_processed"] == 1
    tracks = client.get(f"/api/jobs/{job['id']}/tracks").json()
    assert len(tracks) == 1 and tracks[0]["is_match"]
    assert (tracks[0]["start_s"], tracks[0]["end_s"]) == (3.0, 5.5)
    segs = client.get(f"/api/jobs/{job['id']}/segments").json()
    assert [(s["start_s"], s["end_s"]) for s in segs] == [(3.0, 5.5)]
    dets = client.get(f"/api/jobs/{job['id']}/detections").json()
    assert dets[0]["track_id"] == tracks[0]["id"] and dets[0]["quality"] > 0

    # Rever o track inteiro de uma vez
    r = client.patch(f"/api/tracks/{tracks[0]['id']}/review", json={"status": "rejected", "note": "não é"})
    assert r.json()["review_status"] == "rejected"
    dets = client.get(f"/api/jobs/{job['id']}/detections").json()
    assert {d["review_status"] for d in dets} == {"rejected"}
    assert client.get(f"/api/jobs/{job['id']}/segments").json() == []
    actions = [e["action"] for e in client.get(f"/api/cases/{case['id']}/audit").json()]
    assert "track.reviewed" in actions
    csv_text = client.get(f"/api/jobs/{job['id']}/export/csv").text
    assert "track_id" in csv_text.splitlines()[0] and "quality" in csv_text.splitlines()[0]
    assert "Tracks com correspondência" in client.get(f"/api/jobs/{job['id']}/export/html").text


def test_track_continuations_are_saved(client, tmp_path):
    """Depois de um track ter uma correspondência, os frames seguintes da mesma face são guardados como
    continuação (mesmo acima do limiar) — trajetória completa para o perito."""
    case = client.post("/api/cases", json={"name": "Trajetória"}).json()
    with make_image(tmp_path / "ref.jpg", RED).open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    # vermelho (match) e depois vermelho mais escuro (acima do limiar, mas a mesma "pessoa" para o tracker)
    video = make_video(tmp_path / "v.mp4", [(2.0, 4.0, RED), (4.0, 6.0, (0, 0, 200))], duration_s=8, fps=10)
    with video.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    r = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {
        "sample_fps": 2, "threshold": 0.0005, "candidate_margin": 0.0, "motion_filter": False, "cascade": False}})
    job = wait_for_job(client, r.json()["id"])
    dets = client.get(f"/api/jobs/{job['id']}/detections").json()
    assert {d["is_match"] for d in dets} == {True, False}
    assert len({d["track_id"] for d in dets}) <= 2


def test_cascade_and_detector_receive_job_settings(client, tmp_path):
    """A 1.ª passagem usa a mesma ampliação que a deteção completa e o limiar do detetor segue o parâmetro."""
    from app.services import face_engine

    eng = face_engine.get_engine()
    case = client.post("/api/cases", json={"name": "Parametros"}).json()
    with make_image(tmp_path / "ref.jpg", RED).open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    video = make_video(tmp_path / "v.mp4", [(0.0, 2.0, RED)], duration_s=2, fps=10)
    with video.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    r = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {
        "sample_fps": 2, "upscale": 2.0, "min_confidence": 0.3, "motion_filter": False}})
    assert wait_for_job(client, r.json()["id"])["status"] == "completed"
    assert eng.last_count_upscale == 2.0 and eng.last_min_confidence == 0.3


def test_job_db_error_is_recorded_as_failed(client, tmp_path, monkeypatch):
    """Um erro de base de dados a meio da análise não pode deixar o estado preso em "A decorrer"."""
    from app.services import jobs as jobs_mod

    case, _refs, _video = _setup_case(client, tmp_path)
    original = jobs_mod._VideoAnalyzer._save_detection

    def boom(self, *a, **kw):
        det = original(self, *a, **kw)
        det.crop_path = None  # coluna NOT NULL: o flush falha e a sessão fica à espera de rollback
        self.s.flush()
        return det

    monkeypatch.setattr(jobs_mod._VideoAnalyzer, "_save_detection", boom)
    r = client.post(f"/api/cases/{case['id']}/jobs", json={"params": {"sample_fps": 2, "threshold": 0.05,
                                                                     "motion_filter": False}})
    job = wait_for_job(client, r.json()["id"])
    assert job["status"] == "failed" and "NOT NULL" in job["message"]
