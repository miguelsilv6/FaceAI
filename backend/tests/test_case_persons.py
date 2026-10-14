"""Pessoas do caso (objeto "Pessoa A" com várias fotos), fotos soltas, adicionar/remover faces dos resultados."""

from __future__ import annotations

from sqlmodel import Session, select

from .conftest import new_client
from .fakes import RED, make_image
from .test_discover import _case_with_video, _discover

PW = "girassol-trovoada-marmelo-9"


def _ref(client, case_id, tmp_path, name, color=RED):
    img = make_image(tmp_path / name, color)
    with img.open("rb") as fh:
        return client.post(f"/api/cases/{case_id}/references", files=[("files", (name, fh, "image/jpeg"))]).json()[0]


def test_person_crud_and_photos(client, tmp_path):
    case = client.post("/api/cases", json={"name": "Pessoas"}).json()
    cid = case["id"]
    a = client.post(f"/api/cases/{cid}/persons", json={"name": "Suspeito A", "notes": "casaco azul"}).json()
    assert client.post(f"/api/cases/{cid}/persons", json={"name": "suspeito a"}).status_code == 409
    assert client.post(f"/api/cases/{cid}/persons", json={"name": " "}).status_code == 422
    r1, r2 = _ref(client, cid, tmp_path, "f1.jpg"), _ref(client, cid, tmp_path, "f2.jpg")
    for r in (r1, r2):
        assert client.patch(f"/api/references/{r['id']}", json={"case_person_id": a["id"]}).status_code == 200
    p = client.get(f"/api/cases/{cid}/persons").json()[0]
    assert p["reference_ids"] == [r1["id"], r2["id"]] and p["valid_references"] == 2 and p["notes"] == "casaco azul"
    # Renomear num só sítio
    renamed = client.patch(f"/api/case-persons/{a['id']}", json={"name": "Suspeito Alfa"}).json()
    assert renamed["name"] == "Suspeito Alfa"
    # Pessoa de outro caso não pode ser usada
    other = client.post("/api/cases", json={"name": "Outro"}).json()
    b = client.post(f"/api/cases/{other['id']}/persons", json={"name": "X"}).json()
    assert client.patch(f"/api/references/{r1['id']}", json={"case_person_id": b["id"]}).status_code == 422
    # Soltar uma foto
    assert client.patch(f"/api/references/{r2['id']}", json={"case_person_id": None}).json()["case_person_id"] is None
    # Eliminar a pessoa não apaga as fotos (passam a soltas)
    assert client.delete(f"/api/case-persons/{a['id']}").status_code == 204
    refs = client.get(f"/api/cases/{cid}/references").json()
    assert len(refs) == 2 and all(r["case_person_id"] is None for r in refs)
    actions = [e["action"] for e in client.get(f"/api/cases/{cid}/audit").json()]
    assert {"person.created", "person.updated", "person.deleted", "reference.person_changed"} <= set(actions)
    # Quem não tem acesso ao caso não vê nem altera as pessoas
    outsider = new_client("fora.pessoas", PW)
    assert outsider.get(f"/api/cases/{cid}/persons").status_code == 404
    assert outsider.patch(f"/api/case-persons/{b['id']}", json={"name": "Y"}).status_code == 404


def test_add_face_to_person_and_remove(client, tmp_path):
    case, _ = _case_with_video(client, tmp_path, [(1.0, 3.0, RED)], duration_s=4)
    cid = case["id"]
    person = client.post(f"/api/cases/{cid}/persons", json={"name": "Suspeito A"}).json()
    job = _discover(client, cid)
    det = client.get(f"/api/jobs/{job['id']}/detections").json()[0]
    r = client.post(f"/api/detections/{det['id']}/as-reference", params={"case_person_id": person["id"]})
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["case_person_id"] == person["id"] and ref["source_detection_id"] == det["id"]
    assert client.get(f"/api/cases/{cid}/persons").json()[0]["reference_ids"] == [ref["id"]]
    # Remover: nunca usada numa análise → eliminada
    assert client.post(f"/api/references/{ref['id']}/remove-from-person").json() == {"action": "deleted"}
    assert client.get(f"/api/cases/{cid}/references").json() == []

    # Usada numa análise → só sai da pessoa (fica solta, a análise continua a referenciá-la)
    ref = client.post(f"/api/detections/{det['id']}/as-reference", params={"case_person_id": person["id"]}).json()
    import app.db
    from app.models import Detection

    with Session(app.db.get_engine()) as s:
        d = s.exec(select(Detection)).first()
        d.reference_id = ref["id"]
        s.add(d)
        s.commit()
    assert client.post(f"/api/references/{ref['id']}/remove-from-person").json() == {"action": "detached"}
    assert client.get(f"/api/cases/{cid}/references").json()[0]["case_person_id"] is None


def test_old_labels_migrate_to_persons(client, tmp_path):
    import app.db
    from app.models import CasePerson, ReferenceImage
    from app.services.references import migrate_person_labels

    case = client.post("/api/cases", json={"name": "Antigo"}).json()
    r1, r2, r3 = (_ref(client, case["id"], tmp_path, f"{n}.jpg") for n in ("a", "b", "c"))
    with Session(app.db.get_engine()) as s:
        for rid, label in ((r1["id"], "Suspeito A"), (r2["id"], "Suspeito A"), (r3["id"], "")):
            r = s.get(ReferenceImage, rid)
            r.person_label = label
            s.add(r)
        s.commit()
        assert migrate_person_labels(s) == 1
        assert migrate_person_labels(s) == 0  # idempotente
        persons = s.exec(select(CasePerson)).all()
        assert [p.name for p in persons] == ["Suspeito A"]
        assert s.get(ReferenceImage, r1["id"]).case_person_id == persons[0].id
        assert s.get(ReferenceImage, r3["id"]).case_person_id is None
