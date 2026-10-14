"""Várias pessoas procuradas na mesma passagem pelo vídeo (referências agrupadas por rótulo)."""

from __future__ import annotations

import numpy as np

from .conftest import wait_for_job
from .fakes import BLUE, GREEN, RED, make_image, make_video


def _upload_ref(client, case_id, path, color):
    make_image(path, color)
    with path.open("rb") as fh:
        r = client.post(f"/api/cases/{case_id}/references", files=[("files", (path.name, fh, "image/jpeg"))])
    assert r.status_code == 201, r.text
    return r.json()[0]


def test_two_persons_in_one_pass(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Dois suspeitos"}).json()
    red = _upload_ref(client, case["id"], tmp_path / "a.jpg", RED)
    blue = _upload_ref(client, case["id"], tmp_path / "b.jpg", BLUE)
    for ref, name in ((red, "Suspeito A"), (blue, "  Suspeito   B ")):
        person = client.post(f"/api/cases/{case['id']}/persons", json={"name": name}).json()
        r = client.patch(f"/api/references/{ref['id']}", json={"case_person_id": person["id"]})
        assert r.status_code == 200
    persons = client.get(f"/api/cases/{case['id']}/persons").json()
    assert [(p["name"], p["reference_ids"]) for p in persons] == [("Suspeito A", [red["id"]]),
                                                                 ("Suspeito B", [blue["id"]])]
    video = make_video(tmp_path / "cctv.mp4", [(1.0, 3.0, RED), (4.0, 6.0, BLUE), (7.0, 9.0, GREEN)],
                       duration_s=10, fps=10)
    with video.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("cctv.mp4", fh, "video/mp4"))])
    r = client.post(f"/api/cases/{case['id']}/jobs",
                    json={"params": {"sample_fps": 2, "threshold": 0.05, "candidate_margin": 0.0,
                                     "motion_filter": False, "tracking": True, "track_gap_s": 0.5}})
    assert r.status_code == 201
    assert [(p["id"], p["label"]) for p in r.json()["params"]["persons"]] == [(1, "Suspeito A"), (2, "Suspeito B")]
    job = wait_for_job(client, r.json()["id"])
    assert job["status"] == "completed", job

    dets = client.get(f"/api/jobs/{job['id']}/detections", params={"only_matches": True}).json()
    by_person = {}
    for d in dets:
        by_person.setdefault(d["person_id"], []).append(d["timestamp_s"])
    assert set(by_person) == {1, 2}
    assert all(1.0 <= t <= 3.0 for t in by_person[1]) and all(4.0 <= t <= 6.0 for t in by_person[2])
    tracks = [t for t in client.get(f"/api/jobs/{job['id']}/tracks").json() if t["is_match"]]
    assert sorted(t["person_id"] for t in tracks) == [1, 2]
    assert {t["person_id"]: t["reference_id"] for t in tracks} == {1: red["id"], 2: blue["id"]}

    # Embeddings dos tracks guardados também no modo procurar (para ligar pessoas entre análises)
    from sqlmodel import Session

    import app.db
    from app.models import Job
    from app.services.jobs import job_dir

    with Session(app.db.get_engine()) as s:
        npz = np.load(job_dir(s.get(Job, job["id"])) / "track_embeddings.npz")
    assert sorted(npz["person_ids"].tolist())[:2] == [1, 2] and str(npz["model"]) == "fake_model"

    events = [e["action"] for e in client.get(f"/api/cases/{case['id']}/audit").json()]
    assert events.count("reference.person_changed") == 2 and events.count("person.created") == 2


def test_single_person_keeps_person_id_empty(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Um suspeito"}).json()
    _upload_ref(client, case["id"], tmp_path / "a.jpg", RED)
    video = make_video(tmp_path / "cctv.mp4", [(1.0, 3.0, RED)], duration_s=4, fps=10)
    with video.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("cctv.mp4", fh, "video/mp4"))])
    r = client.post(f"/api/cases/{case['id']}/jobs",
                    json={"params": {"sample_fps": 2, "threshold": 0.05, "motion_filter": False, "tracking": True}})
    job = wait_for_job(client, r.json()["id"])
    assert job["params"]["persons"] == [{"id": 1, "label": "Pessoa", "case_person_id": None,
                                         "reference_ids": job["reference_ids"]}]
    assert {d["person_id"] for d in client.get(f"/api/jobs/{job['id']}/detections").json()} == {None}
    assert {t["person_id"] for t in client.get(f"/api/jobs/{job['id']}/tracks").json()} == {None}


def test_report_and_csv_name_the_person(client, tmp_path):
    test_two_persons_in_one_pass(client, tmp_path)
    jid = max(j["id"] for j in client.get("/api/cases/1/jobs").json())
    csv_text = client.get(f"/api/jobs/{jid}/export/csv").text
    assert "person_label" in csv_text.splitlines()[0] and "Suspeito B" in csv_text
    html = client.get(f"/api/jobs/{jid}/export/html").text
    assert "Pessoas procuradas" in html and "Suspeito A" in html


def test_segments_split_by_person(client, tmp_path):
    test_two_persons_in_one_pass(client, tmp_path)
    jid = max(j["id"] for j in client.get("/api/cases/1/jobs").json())
    segs = client.get(f"/api/jobs/{jid}/segments").json()
    assert {s["person_id"] for s in segs} == {1, 2}
    only_b = client.get(f"/api/jobs/{jid}/segments", params={"person_id": 2}).json()
    assert only_b and all(s["person_id"] == 2 and s["start_s"] >= 4.0 for s in only_b)
