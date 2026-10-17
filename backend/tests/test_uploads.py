"""Upload em blocos com retoma."""

from .fakes import RED, make_image, make_video


def _case(client):
    return client.post("/api/cases", json={"name": "Uploads"}).json()


def test_chunked_video_upload_with_resume(client, tmp_path):
    case = _case(client)
    data = make_video(tmp_path / "cctv.mp4", [(1, 3, RED)], duration_s=4, fps=10).read_bytes()
    up = client.post(f"/api/cases/{case['id']}/uploads",
                     json={"kind": "video", "filename": "cctv.mp4", "size_bytes": len(data)}).json()
    assert up["received_bytes"] == 0 and up["chunk_size"] > 0
    chunk = len(data) // 3 + 1
    # 1.º bloco
    r = client.put(f"/api/uploads/{up['id']}?offset=0", content=data[:chunk])
    assert r.json()["received_bytes"] == chunk
    # cliente "perdeu" o estado e reenvia a partir de 0 → 409 com o offset correto
    r = client.put(f"/api/uploads/{up['id']}?offset=0", content=data[:chunk])
    assert r.status_code == 409 and r.json()["received_bytes"] == chunk
    # concluir antes do fim → 409
    assert client.post(f"/api/uploads/{up['id']}/complete").status_code == 409
    # retoma
    offset = client.get(f"/api/uploads/{up['id']}").json()["received_bytes"]
    while offset < len(data):
        r = client.put(f"/api/uploads/{up['id']}?offset={offset}", content=data[offset:offset + chunk])
        offset = r.json()["received_bytes"]
    video = client.post(f"/api/uploads/{up['id']}/complete").json()
    assert video["filename"] == "cctv.mp4" and video["size_bytes"] == len(data) and video["frame_count"] == 40
    assert client.get(f"/api/cases/{case['id']}/videos").json()[0]["id"] == video["id"]
    # idempotente: repetir (resposta perdida) devolve o mesmo vídeo, sem duplicar
    again = client.post(f"/api/uploads/{up['id']}/complete")
    assert again.status_code == 200 and again.json()["id"] == video["id"]
    assert len(client.get(f"/api/cases/{case['id']}/videos").json()) == 1
    actions = [e["action"] for e in client.get(f"/api/cases/{case['id']}/audit").json()]
    assert "upload.started" in actions and "video.uploaded" in actions


def test_chunked_reference_upload_and_validation(client, tmp_path):
    case = _case(client)
    data = make_image(tmp_path / "ref.jpg", RED).read_bytes()
    up = client.post(f"/api/cases/{case['id']}/uploads",
                     json={"kind": "reference", "filename": "ref.jpg", "size_bytes": len(data)}).json()
    client.put(f"/api/uploads/{up['id']}?offset=0", content=data)
    ref = client.post(f"/api/uploads/{up['id']}/complete").json()
    assert ref["valid"] and ref["face_count"] == 1


def test_upload_rejects_bad_input(client, tmp_path):
    case = _case(client)
    assert client.post(f"/api/cases/{case['id']}/uploads",
                       json={"kind": "video", "filename": "x.exe", "size_bytes": 10}).status_code == 415
    up = client.post(f"/api/cases/{case['id']}/uploads",
                     json={"kind": "video", "filename": "a.mp4", "size_bytes": 10}).json()
    assert client.put(f"/api/uploads/{up['id']}?offset=0", content=b"x" * 11).status_code == 413
    assert client.get(f"/api/uploads/{up['id']}").json()["received_bytes"] == 0
    assert client.delete(f"/api/uploads/{up['id']}").status_code == 204
    assert client.get(f"/api/uploads/{up['id']}").status_code == 404


def test_stale_uploads_are_cleaned(client, tmp_path):
    from datetime import timedelta

    from sqlmodel import Session

    from app.api.uploads import cleanup_stale_uploads
    from app.db import get_engine
    from app.models import Upload, utcnow

    case = _case(client)
    up = client.post(f"/api/cases/{case['id']}/uploads",
                     json={"kind": "video", "filename": "a.mp4", "size_bytes": 10}).json()
    with Session(get_engine()) as s:
        u = s.get(Upload, up["id"])
        part = u.temp_path
        u.created_at = utcnow() - timedelta(days=8)
        s.add(u)
        s.commit()
    assert cleanup_stale_uploads() == 1
    assert client.get(f"/api/uploads/{up['id']}").status_code == 404
    import os
    assert not os.path.exists(part)


def test_invalid_video_upload_is_discarded(client, tmp_path):
    case = _case(client)
    data = b"isto nao e um video" * 10
    up = client.post(f"/api/cases/{case['id']}/uploads",
                     json={"kind": "video", "filename": "x.mp4", "size_bytes": len(data)}).json()
    client.put(f"/api/uploads/{up['id']}?offset=0", content=data)
    assert client.post(f"/api/uploads/{up['id']}/complete").status_code == 422
    assert client.get(f"/api/uploads/{up['id']}").status_code == 404
    assert client.get(f"/api/cases/{case['id']}/videos").json() == []


def test_advertised_chunk_never_exceeds_server_limit():
    from app.api import uploads

    object.__setattr__(uploads.settings, "upload_chunk_mb", 90)
    try:
        assert uploads.chunk_size() == uploads.MAX_CHUNK_BYTES
    finally:
        object.__setattr__(uploads.settings, "upload_chunk_mb", 8)


def _multipart(client, case_id, path, name, location_id=None):
    params = {"location_id": location_id} if location_id else {}
    with path.open("rb") as fh:
        return client.post(f"/api/cases/{case_id}/videos", params=params, files=[("files", (name, fh, "video/mp4"))])


def test_duplicate_video_rejected_in_same_case(client, tmp_path):
    from pathlib import Path

    from app.services.hashing import file_sha256, quick_hash

    case = _case(client)
    path = make_video(tmp_path / "a.mp4", [(1, 3, RED)], duration_s=4, fps=10)
    v = _multipart(client, case["id"], path, "a.mp4").json()[0]
    assert v["sha256"] == file_sha256(path) and v["quick_hash"] == quick_hash(path)
    # o mesmo conteúdo com outro nome e noutro local do caso → 409 e nada fica em disco
    loc = client.post(f"/api/cases/{case['id']}/locations", json={"name": "Outro"}).json()
    before = sorted(p.name for p in Path(v["stored_path"]).parent.iterdir()) if "stored_path" in v else None
    r = _multipart(client, case["id"], path, "copia.mp4", loc["id"])
    assert r.status_code == 409 and f"#{v['id']}" in r.json()["detail"]
    assert r.headers["x-faceai-duplicate-of"] == str(v["id"])
    assert len(client.get(f"/api/cases/{case['id']}/videos").json()) == 1
    if before is not None:
        assert sorted(p.name for p in Path(v["stored_path"]).parent.iterdir()) == before
    events = [e for e in client.get(f"/api/cases/{case['id']}/audit").json()
              if e["action"] == "video.duplicate_rejected"]
    assert events and events[-1]["details"]["duplicate_of"] == v["id"]
    # noutro caso é permitido
    other = client.post("/api/cases", json={"name": "Outro caso"}).json()
    assert _multipart(client, other["id"], path, "a.mp4").status_code == 201

    # upload em blocos: com quick_hash igual → 409 logo na criação (nada é enviado)
    data = path.read_bytes()
    r = client.post(f"/api/cases/{case['id']}/uploads",
                    json={"kind": "video", "filename": "b.mp4", "size_bytes": len(data),
                          "quick_hash": quick_hash(path)})
    assert r.status_code == 409 and r.headers["x-faceai-duplicate-of"] == str(v["id"])
    # sem quick_hash (browser sem crypto.subtle): envia, mas a conclusão recusa e descarta o upload
    up = client.post(f"/api/cases/{case['id']}/uploads",
                     json={"kind": "video", "filename": "c.mp4", "size_bytes": len(data)}).json()
    client.put(f"/api/uploads/{up['id']}?offset=0", content=data)
    r = client.post(f"/api/uploads/{up['id']}/complete")
    assert r.status_code == 409
    assert client.get(f"/api/uploads/{up['id']}").status_code == 404
    assert len(client.get(f"/api/cases/{case['id']}/videos").json()) == 1
    # quick_hash inválido → 422
    r = client.post(f"/api/cases/{case['id']}/uploads",
                    json={"kind": "video", "filename": "d.mp4", "size_bytes": 10, "quick_hash": "xyz"})
    assert r.status_code == 422


def test_quick_hash_formula_and_backfill(client, tmp_path):
    import hashlib

    from sqlmodel import Session

    from app.db import get_engine
    from app.models import Video
    from app.services.hashing import backfill_video_hashes, quick_hash

    small = tmp_path / "small.bin"
    small.write_bytes(b"abc")
    expected = hashlib.sha256((3).to_bytes(8, "little") + b"abc" + b"abc").hexdigest()
    assert quick_hash(small) == expected
    big = tmp_path / "big.bin"
    body = bytes(range(256)) * (3 * 4096)  # 3 MiB
    big.write_bytes(body)
    mib = 1024 * 1024
    assert quick_hash(big) == hashlib.sha256(len(body).to_bytes(8, "little") + body[:mib] + body[-mib:]).hexdigest()

    case = _case(client)
    v = _multipart(client, case["id"], make_video(tmp_path / "x.mp4", [(1, 2, RED)], duration_s=3, fps=10), "x.mp4")
    vid = v.json()[0]["id"]
    with Session(get_engine()) as s:  # simula um vídeo de uma versão anterior, sem hashes
        row = s.get(Video, vid)
        row.sha256, row.quick_hash = "", ""
        s.add(row)
        s.commit()
    assert backfill_video_hashes() >= 1
    with Session(get_engine()) as s:
        row = s.get(Video, vid)
        assert len(row.sha256) == 64 and len(row.quick_hash) == 64


def test_batch_check_before_import(client, tmp_path):
    from app.services.hashing import quick_hash

    case = _case(client)
    a = make_video(tmp_path / "a.mp4", [(1, 3, RED)], duration_s=4, fps=10)
    b = make_video(tmp_path / "b.mp4", [(1, 2, RED)], duration_s=4, fps=10)
    v = _multipart(client, case["id"], a, "a.mp4").json()[0]
    files = [{"name": "a_copia.mp4", "size": a.stat().st_size, "quick_hash": quick_hash(a)},
             {"name": "b.mp4", "size": b.stat().st_size, "quick_hash": quick_hash(b)},
             {"name": "b_outra.mp4", "size": b.stat().st_size, "quick_hash": quick_hash(b)}]
    r = client.post(f"/api/cases/{case['id']}/videos/check", json={"files": files}).json()
    assert r[0]["duplicate_of"] == v["id"] and "já importado" in r[0]["message"]
    assert r[1] == {"name": "b.mp4", "duplicate_of": None}
    assert r[2]["same_as"] == "b.mp4" and "repetido neste lote" in r[2]["message"]
    assert client.post(f"/api/cases/{case['id']}/videos/check",
                       json={"files": [{"name": "x", "size": 1, "quick_hash": "zz"}]}).status_code == 422
