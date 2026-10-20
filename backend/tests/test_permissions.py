"""Permissões por papel no processo, limites do upload simples e validação ao juntar pessoas."""

from __future__ import annotations

import io

from .conftest import new_client, wait_for_job
from .fakes import BLUE, RED, make_image, make_video

PW = "girassol-trovoada-marmelo-9"


def _case(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Permissões"}).json()["id"]
    path = make_video(tmp_path / "v.mp4", [(0.5, 1.5, RED)], duration_s=2, fps=10)
    with path.open("rb") as fh:
        vid = client.post(f"/api/cases/{cid}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))]).json()[0]["id"]
    with make_image(tmp_path / "ref.jpg", RED).open("rb") as fh:
        ref = client.post(f"/api/cases/{cid}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))]).json()[0]
    person = client.post(f"/api/cases/{cid}/persons", json={"name": "Pessoa A"}).json()
    loc = client.post(f"/api/cases/{cid}/locations", json={"name": "Vazio"}).json()
    return cid, vid, ref, person, loc


def test_member_cannot_change_evidence_or_clock(client, tmp_path):
    cid, vid, ref, person, loc = _case(client, tmp_path)
    ana = new_client("ana.p", PW)
    client.post(f"/api/cases/{cid}/members", json={"username": "ana.p", "role": "member"})
    clock = {"video_s": 0, "dvr_time": "2026-05-05T08:00:00"}
    # o membro vê e revê, mas não altera a hora real nem apaga pessoas, fotos ou locais
    assert ana.get(f"/api/cases/{cid}/videos").status_code == 200
    assert ana.put(f"/api/videos/{vid}/clock", json=clock).status_code == 403
    assert ana.patch(f"/api/videos/{vid}/clock/offset", json={"clock_offset_s": 5}).status_code == 403
    assert ana.delete(f"/api/videos/{vid}/clock").status_code == 403
    assert ana.delete(f"/api/references/{ref['id']}").status_code == 403
    assert ana.delete(f"/api/case-persons/{person['id']}").status_code == 403
    assert ana.delete(f"/api/locations/{loc['id']}").status_code == 403
    # o responsável pode
    assert client.put(f"/api/videos/{vid}/clock", json=clock).status_code == 200
    assert client.delete(f"/api/videos/{vid}/clock").status_code == 200
    assert client.delete(f"/api/references/{ref['id']}").status_code == 204
    assert client.delete(f"/api/case-persons/{person['id']}").status_code == 204
    assert client.delete(f"/api/locations/{loc['id']}").status_code == 204


def test_upload_cancel_only_by_starter_or_manager(client, tmp_path):
    cid, *_ = _case(client, tmp_path)
    ana = new_client("ana.u", PW)
    rui = new_client("rui.u", PW)
    for who in ("ana.u", "rui.u"):
        client.post(f"/api/cases/{cid}/members", json={"username": who, "role": "member"})
    body = {"kind": "video", "filename": "x.mp4", "size_bytes": 10}
    up1 = ana.post(f"/api/cases/{cid}/uploads", json=body).json()["id"]
    up2 = ana.post(f"/api/cases/{cid}/uploads", json=body).json()["id"]
    assert rui.delete(f"/api/uploads/{up1}").status_code == 403
    assert ana.delete(f"/api/uploads/{up1}").status_code == 204
    assert client.delete(f"/api/uploads/{up2}").status_code == 204  # responsável


def test_multipart_reference_size_limit(client, monkeypatch):
    from app.api import cases

    cid = client.post("/api/cases", json={"name": "Limite"}).json()["id"]
    monkeypatch.setattr(cases, "MAX_REFERENCE_BYTES", 1024)
    big = io.BytesIO(b"\xff" * 5000)
    r = client.post(f"/api/cases/{cid}/references", files=[("files", ("grande.jpg", big, "image/jpeg"))])
    assert r.status_code == 413
    assert not list((cases.settings.case_dir(cid) / "references").glob("*.jpg"))  # nada fica no disco


def test_multipart_checks_disk_space(client, monkeypatch, tmp_path):
    from app.services import disk

    cid = client.post("/api/cases", json={"name": "Disco"}).json()["id"]
    real = disk.disk_status
    monkeypatch.setattr(disk, "disk_status", lambda: {**real(), "free": 10, "min_free_bytes": 100})
    path = make_video(tmp_path / "v.mp4", [(0.5, 1.5, RED)], duration_s=2, fps=10)
    with path.open("rb") as fh:
        r = client.post(f"/api/cases/{cid}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    assert r.status_code == 507


def test_merge_unknown_person_is_rejected(client, tmp_path):
    cid = client.post("/api/cases", json={"name": "Juntar"}).json()["id"]
    path = make_video(tmp_path / "v.mp4", [(0.5, 2.5, RED), (0.5, 2.5, BLUE)], duration_s=3, fps=10)
    with path.open("rb") as fh:
        client.post(f"/api/cases/{cid}/videos", files=[("files", ("v.mp4", fh, "video/mp4"))])
    params = {"mode": "discover", "sample_fps": 5, "motion_filter": False, "tracking": True}
    job = wait_for_job(client, client.post(f"/api/cases/{cid}/jobs", json={"params": params}).json()["id"])
    r = client.post(f"/api/jobs/{job['id']}/persons/merge", json={"person_ids": [1, 99]})
    assert r.status_code == 404 and "99" in r.json()["detail"]
