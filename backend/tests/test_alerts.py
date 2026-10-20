"""Alertas: pessoas do processo encontradas na descoberta (e em vários locais)."""

from __future__ import annotations

from .conftest import new_client, wait_for_job
from .fakes import BLUE, RED, make_image, make_video

PW = "girassol-trovoada-marmelo-9"


def _setup(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Alertas"}).json()
    cid = case["id"]
    person = client.post(f"/api/cases/{cid}/persons", json={"name": "Pessoa A"}).json()
    with make_image(tmp_path / "a.jpg", RED).open("rb") as fh:
        ref = client.post(f"/api/cases/{cid}/references", files=[("files", ("a.jpg", fh, "image/jpeg"))]).json()[0]
    client.patch(f"/api/references/{ref['id']}", json={"case_person_id": person["id"]})
    locs = [client.post(f"/api/cases/{cid}/locations", json={"name": n}).json() for n in ("Café", "Banco", "Rua")]
    spec = [("cafe.mp4", [(1, 2, RED)], 0), ("banco.mp4", [(1, 2, RED), (3, 4, BLUE)], 1),
            ("rua.mp4", [(1, 2, BLUE)], 2)]
    for name, timeline, li in spec:
        path = make_video(tmp_path / name, timeline, duration_s=5, fps=10)
        with path.open("rb") as fh:
            r = client.post(f"/api/cases/{cid}/videos", params={"location_id": locs[li]["id"]},
                            files=[("files", (name, fh, "video/mp4"))])
        assert r.status_code == 201, r.text
    return cid, person, locs


def _job(client, cid, **params):
    body = {"params": {"sample_fps": 2, "threshold": 0.05, "motion_filter": False, "tracking": True, **params}}
    r = client.post(f"/api/cases/{cid}/jobs", json=body)
    assert r.status_code == 201, r.text
    return wait_for_job(client, r.json()["id"])


def test_discover_alerts_person_in_several_locations(client, tmp_path):
    cid, person, locs = _setup(client, tmp_path)
    job = _job(client, cid, mode="discover")
    assert job["status"] == "completed" and "alerta" in job["message"]
    alerts = client.get(f"/api/jobs/{job['id']}/alerts").json()
    assert len(alerts) == 1  # só a Pessoa A (vermelho); o azul não tem fotos no processo
    a = alerts[0]
    assert a["case_person_id"] == person["id"] and a["case_person_name"] == "Pessoa A"
    assert a["highlighted"] and sorted(a["location_ids"]) == sorted([locs[0]["id"], locs[1]["id"]])
    assert set(a["location_names"]) == {"Café", "Banco"} and a["status"] == "new" and a["reference_id"]
    assert a["discovered_person_id"] is not None and a["similarity"] > 0.9
    assert [c["alerts_new"] for c in client.get("/api/cases").json() if c["id"] == cid] == [1]
    assert any(e["action"] == "alert.created" for e in client.get(f"/api/cases/{cid}/audit").json())
    # IDOR
    other = new_client("zeca.alertas", PW)
    assert other.get(f"/api/jobs/{job['id']}/alerts").status_code == 404
    assert other.patch(f"/api/alerts/{a['id']}", json={"status": "dismissed"}).status_code == 404
    # confirmar: a pessoa descoberta fica confirmada (todos os tracks)
    r = client.patch(f"/api/alerts/{a['id']}", json={"status": "confirmed", "note": "é a A"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed" and r.json()["reviewed_by"]
    persons = {p["person_id"]: p for p in client.get(f"/api/jobs/{job['id']}/persons").json()}
    assert persons[a["discovered_person_id"]]["review_status"] == "confirmed"
    assert client.get(f"/api/cases/{cid}/alerts", params={"status": "new"}).json() == []
    actions = [e["action"] for e in client.get(f"/api/cases/{cid}/audit").json()]
    assert "alert.confirmed" in actions
    assert client.patch(f"/api/alerts/{a['id']}", json={"status": "bogus"}).status_code == 422


def test_alert_thresholds_and_opt_out(client, tmp_path):
    cid, person, _ = _setup(client, tmp_path)
    j3 = _job(client, cid, mode="discover", alert_min_locations=3)
    alerts = client.get(f"/api/jobs/{j3['id']}/alerts").json()
    assert len(alerts) == 1 and not alerts[0]["highlighted"]  # 2 locais < 3
    client.patch(f"/api/alerts/{alerts[0]['id']}", json={"status": "dismissed"})
    off = _job(client, cid, mode="discover", watch_persons=False)
    off_alerts = client.get(f"/api/jobs/{off['id']}/alerts").json()
    assert not [x for x in off_alerts if x["kind"] == "person"]  # sem alertas de pessoas do processo
    # sem alertas de pessoas do processo, a pessoa vista no Café e no Banco é assinalada como desconhecida em 2 locais
    assert [x["kind"] for x in off_alerts].count("places") == 1
    assert all(x["linked_job_id"] == j3["id"] for x in off_alerts if x["kind"] == "linked")  # ligação à anterior
    # modo procurar: a Pessoa A com correspondências em 2 locais → alerta destacado
    match = _job(client, cid)
    m = client.get(f"/api/jobs/{match['id']}/alerts").json()
    assert len(m) == 1 and m[0]["kind"] == "person" and m[0]["highlighted"] and m[0]["discovered_person_id"] is None
    # eliminar a pessoa elimina os seus alertas; eliminar a análise também
    r = client.request("DELETE", f"/api/jobs/{match['id']}", json={"confirm_job_id": match["id"], "reason": "t"})
    assert r.status_code == 204
    assert client.delete(f"/api/case-persons/{person['id']}").status_code == 204
    assert [x for x in client.get(f"/api/cases/{cid}/alerts").json() if x["kind"] == "person"] == []


def _video(client, cid, tmp_path, name, timeline, location_id):
    path = make_video(tmp_path / name, timeline, duration_s=4, fps=10)
    with path.open("rb") as fh:
        r = client.post(f"/api/cases/{cid}/videos", params={"location_id": location_id},
                        files=[("files", (name, fh, "video/mp4"))])
    assert r.status_code == 201, r.text
    return r.json()[0]["id"]


def test_unknown_person_in_several_locations(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Desconhecidos"}).json()["id"]
    locs = [client.post(f"/api/cases/{cid}/locations", json={"name": n}).json()["id"] for n in ("Café", "Banco")]
    _video(client, cid, tmp_path, "cafe.mp4", [(0.5, 3.5, BLUE)], locs[0])
    _video(client, cid, tmp_path, "banco.mp4", [(0.5, 3.5, BLUE), (0.5, 3.5, RED)], locs[1])
    job = _job(client, cid, mode="discover")
    alerts = client.get(f"/api/jobs/{job['id']}/alerts").json()
    places = [a for a in alerts if a["kind"] == "places"]
    assert len(places) == 1 and places[0]["highlighted"] and places[0]["case_person_id"] is None
    assert set(places[0]["location_names"]) == {"Café", "Banco"} and places[0]["case_person_name"] is None
    assert "desconhecida(s) em vários locais" in job["message"]
    # a mesma pessoa, agora com fotos no processo: fica o alerta "person" e não o "places"
    person = client.post(f"/api/cases/{cid}/persons", json={"name": "Pessoa Azul"}).json()
    with make_image(tmp_path / "azul.jpg", BLUE).open("rb") as fh:
        ref = client.post(f"/api/cases/{cid}/references", files=[("files", ("azul.jpg", fh, "image/jpeg"))]).json()[0]
    client.patch(f"/api/references/{ref['id']}", json={"case_person_id": person["id"]})
    again = _job(client, cid, mode="discover")
    kinds = [a["kind"] for a in client.get(f"/api/jobs/{again['id']}/alerts").json() if a["kind"] != "linked"]
    assert kinds == ["person"]


def test_linked_across_analyses_of_the_same_case(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Ligações"}).json()["id"]
    la, lb = (client.post(f"/api/cases/{cid}/locations", json={"name": n}).json()["id"] for n in ("A", "B"))
    va = _video(client, cid, tmp_path, "a.mp4", [(0.5, 3.5, RED)], la)
    vb = _video(client, cid, tmp_path, "b.mp4", [(0.5, 3.5, RED), (0.5, 3.5, BLUE)], lb)
    first = wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json={
        "params": {"mode": "discover", "sample_fps": 2, "motion_filter": False}, "video_ids": [va]}).json()["id"])
    second = wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json={
        "params": {"mode": "discover", "sample_fps": 2, "motion_filter": False}, "video_ids": [vb]}).json()["id"])
    linked = [a for a in client.get(f"/api/jobs/{second['id']}/alerts").json() if a["kind"] == "linked"]
    assert len(linked) == 1  # só o vermelho existe nas duas análises
    a = linked[0]
    assert a["linked_job_id"] == first["id"] and a["linked_person_id"] == 1 and a["linked_mode"] == "discover"
    assert a["linked_track"]["video_id"] == va and set(a["location_names"]) == {"A", "B"}
    assert "ligação(ões) a outras análises" in second["message"]
    # outro processo com a mesma pessoa não é considerado
    other = client.post("/api/cases", json={"name": "Outro"}).json()["id"]
    lo = client.post(f"/api/cases/{other}/locations", json={"name": "X"}).json()["id"]
    vo = _video(client, other, tmp_path, "o.mp4", [(0.5, 3.5, RED)], lo)
    third = wait_for_job(client, client.post(f"/api/cases/{other}/jobs", json={
        "params": {"mode": "discover", "sample_fps": 2, "motion_filter": False}, "video_ids": [vo]}).json()["id"])
    assert [a for a in client.get(f"/api/jobs/{third['id']}/alerts").json() if a["kind"] == "linked"] == []
