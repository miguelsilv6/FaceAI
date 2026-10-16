"""Locais do caso: cada local agrupa os vídeos (câmaras) e a análise pode abranger vários locais."""

from __future__ import annotations

import csv
import io

from .conftest import new_client, wait_for_job
from .fakes import BLUE, RED, make_image, make_video

PW = "girassol-trovoada-marmelo-9"


def _case(client, name="Locais"):
    return client.post("/api/cases", json={"name": name}).json()


def _location(client, case_id, name):
    r = client.post(f"/api/cases/{case_id}/locations", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _video(client, case_id, tmp_path, name, timeline, location_id=None):
    path = make_video(tmp_path / name, timeline, duration_s=4, fps=10)
    params = {"location_id": location_id} if location_id is not None else {}
    with path.open("rb") as fh:
        r = client.post(f"/api/cases/{case_id}/videos", params=params, files=[("files", (name, fh, "video/mp4"))])
    assert r.status_code == 201, r.text
    return r.json()[0]


def _person(client, case_id, tmp_path, name, color):
    person = client.post(f"/api/cases/{case_id}/persons", json={"name": name}).json()
    img = make_image(tmp_path / f"{name}.jpg", color)
    with img.open("rb") as fh:
        ref = client.post(f"/api/cases/{case_id}/references",
                          files=[("files", (f"{name}.jpg", fh, "image/jpeg"))]).json()[0]
    client.patch(f"/api/references/{ref['id']}", json={"case_person_id": person["id"]})
    return person


def test_location_crud_and_audit(client, tmp_path):
    case = _case(client)
    x = _location(client, case["id"], "Estação")
    assert x["name"] == "Estação" and x["video_ids"] == []
    # nome repetido (sem distinção de maiúsculas) → 409
    assert client.post(f"/api/cases/{case['id']}/locations", json={"name": " estação "}).status_code == 409
    assert client.post(f"/api/cases/{case['id']}/locations", json={"name": "  "}).status_code == 422
    y = _location(client, case["id"], "Rua")
    assert client.patch(f"/api/locations/{y['id']}", json={"name": "Estação"}).status_code == 409
    assert client.patch(f"/api/locations/{y['id']}", json={"name": "Rua Direita"}).json()["name"] == "Rua Direita"
    v = _video(client, case["id"], tmp_path, "cam1.mp4", [(1, 2, RED)], x["id"])
    assert v["location_id"] == x["id"] and v["camera_name"] == ""
    locs = {loc["name"]: loc for loc in client.get(f"/api/cases/{case['id']}/locations").json()}
    assert locs["Estação"]["video_ids"] == [v["id"]] and "Geral" not in locs
    # local com vídeos não pode ser eliminado; vazio pode
    r = client.delete(f"/api/locations/{x['id']}")
    assert r.status_code == 409 and "Mova" in r.json()["detail"]
    assert client.delete(f"/api/locations/{y['id']}").status_code == 204
    assert client.delete(f"/api/locations/{y['id']}").status_code == 404
    actions = [e["action"] for e in client.get(f"/api/cases/{case['id']}/audit").json()]
    assert {"location.created", "location.updated", "location.deleted"} <= set(actions)
    # IDOR: outro utilizador sem acesso ao caso → 404
    other = new_client("zeca.locais", PW)
    assert other.get(f"/api/cases/{case['id']}/locations").status_code == 404
    assert other.patch(f"/api/locations/{x['id']}", json={"name": "Hack"}).status_code == 404
    assert other.delete(f"/api/locations/{x['id']}").status_code == 404


def test_upload_default_location_and_video_update(client, tmp_path):
    case = _case(client)
    # sem local indicado → "Geral" (criado automaticamente)
    v = _video(client, case["id"], tmp_path, "antigo.mp4", [(1, 2, RED)])
    locs = client.get(f"/api/cases/{case['id']}/locations").json()
    assert [loc["name"] for loc in locs] == ["Geral"] and v["location_id"] == locs[0]["id"]
    x = _location(client, case["id"], "Café")
    # local de outro caso → 422 (multipart, em blocos e PATCH)
    other_case = _case(client, "Outro")
    z = _location(client, other_case["id"], "Alheio")
    path = make_video(tmp_path / "x.mp4", [(1, 2, RED)], duration_s=2, fps=10)
    with path.open("rb") as fh:
        r = client.post(f"/api/cases/{case['id']}/videos", params={"location_id": z["id"]},
                        files=[("files", ("x.mp4", fh, "video/mp4"))])
    assert r.status_code == 422
    r = client.post(f"/api/cases/{case['id']}/uploads",
                    json={"kind": "video", "filename": "x.mp4", "size_bytes": 10, "location_id": z["id"]})
    assert r.status_code == 422
    assert client.patch(f"/api/videos/{v['id']}", json={"location_id": z["id"]}).status_code == 422
    # mover e dar nome à câmara
    r = client.patch(f"/api/videos/{v['id']}", json={"location_id": x["id"], "camera_name": "  Entrada  norte "})
    assert r.status_code == 200 and r.json()["location_id"] == x["id"] and r.json()["camera_name"] == "Entrada norte"
    ev = [e for e in client.get(f"/api/cases/{case['id']}/audit").json() if e["action"] == "video.updated"][-1]
    assert ev["details"]["changes"]["location"][1]["name"] == "Café"
    assert ev["details"]["changes"]["camera_name"] == ["", "Entrada norte"]
    # upload em blocos para um local
    data = make_video(tmp_path / "blocos.mp4", [(1, 2, RED)], duration_s=2, fps=10).read_bytes()
    up = client.post(f"/api/cases/{case['id']}/uploads",
                     json={"kind": "video", "filename": "blocos.mp4", "size_bytes": len(data),
                           "location_id": x["id"]}).json()
    # com um upload por concluir, o local não pode ser eliminado (mesmo sem vídeos ainda)
    client.patch(f"/api/videos/{v['id']}", json={"location_id": locs[0]["id"]})
    assert client.delete(f"/api/locations/{x['id']}").status_code == 409
    client.put(f"/api/uploads/{up['id']}?offset=0", content=data)
    video = client.post(f"/api/uploads/{up['id']}/complete").json()
    assert video["location_id"] == x["id"]
    assert new_client("zeca.videos", PW).patch(f"/api/videos/{v['id']}", json={"camera_name": "x"}).status_code == 404


def test_migration_assigns_general_location(client, tmp_path):
    from sqlmodel import Session, select

    from app.db import get_engine
    from app.models import Location, Video
    from app.services.locations import migrate_locations

    case = _case(client)
    v1 = _video(client, case["id"], tmp_path, "a.mp4", [(1, 2, RED)])
    v2 = _video(client, case["id"], tmp_path, "b.mp4", [(1, 2, RED)])
    with Session(get_engine()) as s:  # simula uma base de dados anterior aos locais
        for loc in s.exec(select(Location)).all():
            for v in s.exec(select(Video).where(Video.location_id == loc.id)).all():
                v.location_id = None
                s.add(v)
            s.flush()
            s.delete(loc)
        s.commit()
        assert migrate_locations(s) == 2
        assert migrate_locations(s) == 0  # idempotente
        locs = s.exec(select(Location).where(Location.case_id == case["id"])).all()
        assert [(loc.name, loc.created_by) for loc in locs] == [("Geral", "migração")]
        assert {s.get(Video, v1["id"]).location_id, s.get(Video, v2["id"]).location_id} == {locs[0].id}


def test_job_by_locations_two_persons(client, tmp_path):
    case = _case(client, "Pessoas × locais")
    cid = case["id"]
    _person(client, cid, tmp_path, "Pessoa A", RED)
    _person(client, cid, tmp_path, "Pessoa B", BLUE)
    x, y, z = (_location(client, cid, n) for n in ("X", "Y", "Z"))
    x1 = _video(client, cid, tmp_path, "x1.mp4", [(1, 2, RED)], x["id"])
    x2 = _video(client, cid, tmp_path, "x2.mp4", [(1, 2, BLUE)], x["id"])
    y1 = _video(client, cid, tmp_path, "y1.mp4", [(1, 2, RED), (2.5, 3.5, BLUE)], y["id"])
    _video(client, cid, tmp_path, "z1.mp4", [(1, 2, RED)], z["id"])
    client.patch(f"/api/videos/{x1['id']}", json={"camera_name": "Entrada"})
    # local de outro caso → 422
    other = _location(client, _case(client, "Outro")["id"], "W")
    assert client.post(f"/api/cases/{cid}/jobs", json={"location_ids": [other["id"]]}).status_code == 422
    # local vazio → sem vídeos → 422
    empty = _location(client, cid, "Vazio")
    assert client.post(f"/api/cases/{cid}/jobs", json={"location_ids": [empty["id"]]}).status_code == 422

    job = client.post(f"/api/cases/{cid}/jobs",
                      json={"location_ids": [x["id"], y["id"]],
                            "params": {"sample_fps": 2, "threshold": 0.05, "motion_filter": False,
                                       "tracking": True, "track_gap_s": 0.5}}).json()
    assert sorted(job["video_ids"]) == sorted([x1["id"], x2["id"], y1["id"]])
    assert [(loc["name"], sorted(loc["video_ids"])) for loc in job["params"]["locations"]] == [
        ("X", sorted([x1["id"], x2["id"]])), ("Y", [y1["id"]])]
    labels = {p["label"]: p["id"] for p in job["params"]["persons"]}
    assert set(labels) == {"Pessoa A", "Pessoa B"}
    wait_for_job(client, job["id"])
    dets = [d for d in client.get(f"/api/jobs/{job['id']}/detections").json() if d["is_match"]]
    found = {(d["video_id"], d["person_id"]) for d in dets}
    assert (x1["id"], labels["Pessoa A"]) in found and (x2["id"], labels["Pessoa B"]) in found
    assert {(y1["id"], labels["Pessoa A"]), (y1["id"], labels["Pessoa B"])} <= found
    # união: local + vídeo avulso
    j2 = client.post(f"/api/cases/{cid}/jobs", json={"location_ids": [y["id"]], "video_ids": [x2["id"]],
                                                    "params": {"sample_fps": 1}}).json()
    assert sorted(j2["video_ids"]) == sorted([x2["id"], y1["id"]])
    wait_for_job(client, j2["id"])

    # percurso: local e câmara em cada segmento e vídeo
    route = client.get(f"/api/cases/{cid}/route", params={"job_ids": str(job["id"])}).json()
    seg = next(s for s in route["unsynced"] if s["video_id"] == x1["id"])
    assert seg["location_name"] == "X" and seg["camera_name"] == "Entrada" and seg["camera_label"] == "X · Entrada"
    vids = {v["id"]: v for v in route["videos"]}
    assert vids[x2["id"]]["camera_label"] == "X · x2.mp4"
    # painel: por local (Z sem correspondências nesta análise, mas com uma câmara)
    d = client.get(f"/api/cases/{cid}/dashboard", params={"job_ids": str(job["id"])}).json()
    per_loc = {p["name"]: p for p in d["per_location"]}
    assert per_loc["Z"]["cameras"] == 1 and per_loc["Z"]["matches"] == 0
    assert per_loc["X"]["cameras"] == 2 and per_loc["X"]["matches"] > 0 and per_loc["Y"]["matches"] > 0
    assert per_loc["X"]["matches"] + per_loc["Y"]["matches"] == d["review"]["matches"]
    assert d["material"]["locations"] == 4  # X, Y, Z e Vazio
    cams = {c["video_id"]: c for c in d["per_camera"]}
    assert cams[x1["id"]]["camera_label"] == "X · Entrada"
    # relatório CSV/JSON
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/jobs/{job['id']}/export/csv").text.lstrip("﻿"))))
    row = next(r for r in rows if int(r["video_id"]) == x1["id"])
    assert row["location_name"] == "X" and row["camera_name"] == "Entrada"
    js = client.get(f"/api/jobs/{job['id']}/export/json").json()
    assert [loc["name"] for loc in js["locations"]] == ["X", "Y"]
    assert {v["id"]: v["camera_label"] for v in js["videos"]}[x1["id"]] == "X · Entrada"
    html = client.get(f"/api/jobs/{job['id']}/export/html").text
    assert "X · Entrada" in html
    # mudar o nome do local depois da análise: o relatório indica o nome no momento da análise
    client.patch(f"/api/locations/{x['id']}", json={"name": "X renomeado"})
    assert "na análise: X" in client.get(f"/api/jobs/{job['id']}/export/html").text
    # eliminar o caso apaga também os locais
    r = client.request("DELETE", f"/api/cases/{cid}", json={"confirm_case_id": cid, "reason": "teste"})
    assert r.status_code == 204, r.text
    from sqlmodel import Session, select

    from app.db import get_engine
    from app.models import Location

    with Session(get_engine()) as s:
        assert not s.exec(select(Location).where(Location.case_id == cid)).all()
