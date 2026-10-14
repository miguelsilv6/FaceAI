"""Ligar pessoas entre análises: procura nos casos acessíveis, só com o mesmo modelo."""

from __future__ import annotations

import numpy as np
from sqlmodel import Session

from .conftest import new_client, wait_for_job
from .fakes import BLUE, RED, make_image, make_video

PW = "girassol-trovoada-marmelo-9"


def _case_with_job(client, tmp_path, name, timeline):
    case = client.post("/api/cases", json={"name": name}).json()
    img = make_image(tmp_path / f"{name}.jpg", RED)
    with img.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    video = make_video(tmp_path / f"{name}.mp4", timeline, duration_s=6, fps=10)
    with video.open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/videos", files=[("files", ("cam.mp4", fh, "video/mp4"))])
    r = client.post(f"/api/cases/{case['id']}/jobs",
                    json={"params": {"sample_fps": 2, "threshold": 0.05, "motion_filter": False, "tracking": True,
                                     "track_gap_s": 0.5}})
    job = wait_for_job(client, r.json()["id"])
    tracks = client.get(f"/api/jobs/{job['id']}/tracks").json()
    return case, job, tracks


def test_similar_tracks_across_accessible_cases(client, tmp_path):
    case_a, job_a, tracks_a = _case_with_job(client, tmp_path, "A", [(1.0, 3.0, RED)])
    case_b, job_b, tracks_b = _case_with_job(client, tmp_path, "B", [(1.0, 2.0, RED), (3.5, 5.0, BLUE)])
    red_a = next(t for t in tracks_a if t["is_match"])
    red_b = next(t for t in tracks_b if t["is_match"])

    out = client.get(f"/api/tracks/{red_a['id']}/similar").json()
    assert [(r["case_id"], r["track_id"]) for r in out["results"]] == [(case_b["id"], red_b["id"])]
    assert out["results"][0]["case_name"] == "B" and out["results"][0]["similarity"] > 0.9
    assert out["jobs_searched"] == 1 and out["jobs_not_indexed"] == 0
    # scope=case: só o próprio caso (a análise de origem é excluída)
    assert client.get(f"/api/tracks/{red_a['id']}/similar", params={"scope": "case"}).json()["results"] == []

    # Analista com acesso só ao caso A não vê o caso B; sem acesso ao A → 404
    ana = new_client("ana.link", PW)
    client.post(f"/api/cases/{case_a['id']}/members", json={"username": "ana.link"})
    assert ana.get(f"/api/tracks/{red_a['id']}/similar").json()["results"] == []
    assert ana.get(f"/api/tracks/{red_b['id']}/similar").status_code == 404
    client.post(f"/api/cases/{case_b['id']}/members", json={"username": "ana.link"})
    assert len(ana.get(f"/api/tracks/{red_a['id']}/similar").json()["results"]) == 1

    # Por pessoa (média dos tracks da pessoa): a análise A tem uma só pessoa → person_id 1 no índice
    assert client.get(f"/api/jobs/{job_a['id']}/persons/1/similar").json()["results"][0]["track_id"] == red_b["id"]

    # Modelos diferentes não se comparam
    import app.db
    from app.models import Job
    from app.services.jobs import job_dir

    with Session(app.db.get_engine()) as s:
        path = job_dir(s.get(Job, job_b["id"])) / "track_embeddings.npz"
    with np.load(path) as z:
        data = {k: z[k] for k in z.files}
    np.savez_compressed(path, **{**data, "model": np.array("outro_modelo")})
    out = client.get(f"/api/tracks/{red_a['id']}/similar").json()
    assert out["results"] == [] and out["jobs_other_model"] == 1

    events = [e for e in client.get(f"/api/cases/{case_a['id']}/audit").json() if e["action"] == "person.linked_search"]
    assert events and events[0]["details"]["cases_with_results"] == [case_b["id"]]
    assert client.get(f"/api/tracks/{red_a['id']}/similar", params={"scope": "x"}).status_code == 422
