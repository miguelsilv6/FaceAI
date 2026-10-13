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
    assert client.post(f"/api/uploads/{up['id']}/complete").status_code == 409  # já concluído
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
