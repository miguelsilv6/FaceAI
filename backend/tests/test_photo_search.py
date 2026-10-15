"""Pesquisa por foto: procurar uma pessoa do caso (pelas suas fotos) nas análises já feitas de outros casos."""

from __future__ import annotations

from .conftest import new_client
from .fakes import BLUE, RED, make_image
from .test_linking import _case_with_job

PW = "girassol-trovoada-marmelo-9"


def _upload_ref(client, case_id, tmp_path, name, color):
    img = make_image(tmp_path / name, color)
    with img.open("rb") as fh:
        return client.post(f"/api/cases/{case_id}/references", files=[("files", (name, fh, "image/jpeg"))]).json()[0]


def test_search_case_person_in_other_cases(client, tmp_path):
    # Caso B: vídeo com RED e BLUE, já analisado (o índice dos tracks fica gravado)
    case_b, _job_b, tracks_b = _case_with_job(client, tmp_path, "B", [(1.0, 2.0, RED), (3.5, 5.0, BLUE)])
    red_b = next(t for t in tracks_b if t["is_match"])

    # Caso A: só a pessoa "Suspeito" com uma foto RED (sem vídeos nem análises)
    case_a = client.post("/api/cases", json={"name": "A"}).json()
    person = client.post(f"/api/cases/{case_a['id']}/persons", json={"name": "Suspeito"}).json()
    ref = _upload_ref(client, case_a["id"], tmp_path, "suspeito.jpg", RED)
    client.patch(f"/api/references/{ref['id']}", json={"case_person_id": person["id"]})

    out = client.get(f"/api/case-persons/{person['id']}/search").json()
    assert [(r["case_id"], r["track_id"]) for r in out["results"]] == [(case_b["id"], red_b["id"])]
    assert out["results"][0]["similarity"] > 0.9 and out["jobs_searched"] == 1
    # O mesmo pela foto solta (aqui: a foto da pessoa)
    assert client.get(f"/api/references/{ref['id']}/search").json()["results"][0]["track_id"] == red_b["id"]
    # scope=case: só o caso A, que não tem análises
    assert client.get(f"/api/case-persons/{person['id']}/search", params={"scope": "case"}).json()["results"] == []

    # Analista com acesso só ao caso A não vê o caso B
    ana = new_client("ana.foto", PW)
    client.post(f"/api/cases/{case_a['id']}/members", json={"username": "ana.foto"})
    assert ana.get(f"/api/case-persons/{person['id']}/search").json()["results"] == []
    client.post(f"/api/cases/{case_b['id']}/members", json={"username": "ana.foto"})
    assert len(ana.get(f"/api/case-persons/{person['id']}/search").json()["results"]) == 1

    events = [e for e in client.get(f"/api/cases/{case_a['id']}/audit").json() if e["action"] == "person.photo_search"]
    assert events and events[-1]["details"]["cases_with_results"] == [case_b["id"]]
    assert events[-1]["details"]["reference_ids"] == [ref["id"]]


def test_search_validation_and_access(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Vazio"}).json()
    person = client.post(f"/api/cases/{case['id']}/persons", json={"name": "Sem fotos"}).json()
    assert client.get(f"/api/case-persons/{person['id']}/search").status_code == 422
    assert client.get(f"/api/case-persons/{person['id']}/search", params={"scope": "x"}).status_code == 422
    ref = _upload_ref(client, case["id"], tmp_path, "azul.jpg", BLUE)
    assert client.get(f"/api/references/{ref['id']}/search", params={"threshold": 9}).status_code == 422
    # Sem acesso ao caso → 404 (anti-IDOR)
    bob = new_client("bob.foto", PW)
    assert bob.get(f"/api/case-persons/{person['id']}/search").status_code == 404
    assert bob.get(f"/api/references/{ref['id']}/search").status_code == 404
