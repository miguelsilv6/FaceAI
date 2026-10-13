"""Complemento por vestuário: assinatura de cor, associação face→corpo e o fluxo completo com motores falsos."""

import numpy as np
import pytest

from app.services import body_analysis, body_engine
from app.services.body_analysis import FaceLink, link_faces
from app.services.body_engine import PersonObservation
from app.services.clothing import color_distance, color_signature, frame_gain

from .conftest import wait_for_job
from .fakes import RED, FakeBodyEngine, make_image, make_people_video

ORANGE, NAVY, BLUE, GREY = (20, 120, 230), (90, 40, 20), (220, 60, 30), (128, 128, 128)


def _person(top, bottom, h=200, w=80, light=1.0):
    img = np.full((h, w, 3), (200, 200, 200), np.uint8)
    img[int(h * .2):int(h * .55)] = top
    img[int(h * .55):] = bottom
    return np.clip(img * light, 0, 255).astype(np.uint8)


def test_color_signature_names_and_distance():
    scene = np.full((90, 160, 3), (120, 125, 130), np.uint8)
    a = color_signature(_person((30, 30, 200), NAVY), frame_gain(scene))
    assert a.names == {"tronco": "vermelho", "pernas": "azul-escuro"}
    assert color_distance(a, color_signature(_person((30, 30, 200), NAVY, light=0.7))) == pytest.approx(0, abs=0.05)
    # Câmara com outro balanço de brancos: a normalização pelo frame compensa.
    tint = np.array([1.25, 1.0, 0.8])
    tinted = np.clip(_person((30, 30, 200), NAVY) * tint, 0, 255).astype(np.uint8)
    assert color_distance(a, color_signature(tinted, frame_gain(np.clip(scene * tint, 0, 255).astype(np.uint8)))) < 0.1
    assert 0.3 < color_distance(a, color_signature(_person((240, 240, 240), NAVY))) < 0.7  # só o tronco muda
    assert color_distance(a, color_signature(_person((40, 160, 40), (20, 20, 20)))) > 0.9


def test_link_faces_to_bodies():
    persons = [PersonObservation(100, 50, 60, 200, 0.9), PersonObservation(0, 0, 300, 240, 0.6)]
    faces = [FaceLink((115, 55, 30, 30), 0.1, None), FaceLink((250, 200, 20, 20), 0.9, None)]
    # A 1.ª face fica no corpo mais pequeno que a enquadra; a 2.ª está demasiado em baixo para ser de alguém.
    assert link_faces(persons, faces) == {0: 0}


@pytest.fixture()
def fake_body(monkeypatch):
    body_engine.set_body_engine(FakeBodyEngine())
    monkeypatch.setattr(body_analysis, "MIN_BODY_QUALITY", 0.0)  # retângulos lisos: nitidez ~0
    yield
    body_engine.set_body_engine(None)


def _upload_video(client, case_id, path):
    with path.open("rb") as fh:
        r = client.post(f"/api/cases/{case_id}/videos", files=[("files", (path.name, fh, "video/mp4"))])
    assert r.status_code == 201, r.text
    return r.json()[0]


def test_clothing_complements_face_matches(client, tmp_path, fake_body):
    case = client.post("/api/cases", json={"name": "Loja"}).json()
    with make_image(tmp_path / "ref.jpg", RED).open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    # Câmara A (entrada): a pessoa de frente (face vermelha = a referência) com camisola laranja e calças azuis.
    a = _upload_video(client, case["id"], make_people_video(tmp_path / "camA.mp4", [
        {"x": 40, "start": 0, "end": 6, "torso": ORANGE, "legs": NAVY, "head": RED}]))
    # Câmara B (corredor): a mesma roupa, de costas (sem face); e outra pessoa de azul e cinzento.
    b = _upload_video(client, case["id"], make_people_video(tmp_path / "camB.mp4", [
        {"x": 40, "start": 1, "end": 5, "torso": ORANGE, "legs": NAVY, "head": None},
        {"x": 200, "start": 0, "end": 6, "torso": BLUE, "legs": GREY, "head": None}]))
    # Câmara C, dois dias depois: alguém com roupa igual, fora da janela temporal.
    c = _upload_video(client, case["id"], make_people_video(tmp_path / "camC.mp4", [
        {"x": 40, "start": 0, "end": 6, "torso": ORANGE, "legs": NAVY, "head": None}]))
    for vid, when in ((a["id"], "2026-10-12T14:00:00"), (b["id"], "2026-10-12T14:02:00"),
                      (c["id"], "2026-10-14T10:00:00")):
        assert client.put(f"/api/videos/{vid}/clock", json={"video_s": 0, "dvr_time": when}).status_code == 200

    params = {"sample_fps": 2, "threshold": 0.05, "motion_filter": False, "clothing": True,
              "reid_threshold": 0.02, "color_threshold": 0.6, "min_body_height": 40}
    job = wait_for_job(client, client.post(f"/api/cases/{case['id']}/jobs", json={"params": params}).json()["id"])
    assert job["status"] == "completed", job
    assert job["clothing_samples"] == 1 and "1 aparição(ões) só pelo vestuário" in job["message"]
    summary = job["clothing_summary"]
    assert summary["excluded_by_window"] == 1 and summary["gallery_colors"]["tronco"] == "laranja"

    bodies = client.get(f"/api/jobs/{job['id']}/tracks", params={"kind": "body"}).json()
    by_video = {t["video_id"]: t for t in bodies}
    assert set(by_video) == {a["id"], b["id"]}  # a pessoa de azul e a da câmara C não aparecem
    assert by_video[a["id"]]["evidence"] == "face+vestuário" and by_video[a["id"]]["face_track_id"]
    cand = by_video[b["id"]]
    assert cand["evidence"] == "vestuário" and cand["review_status"] == "pending"
    assert cand["colors"] == {"tronco": "laranja", "pernas": "azul-escuro"} and cand["reid_distance"] <= 0.02
    assert client.get(f"/api/jobs/{job['id']}/tracks", params={"kind": "face"}).json()  # faciais à parte

    segs = client.get(f"/api/jobs/{job['id']}/segments").json()
    assert {s["video_id"]: s["evidence"] for s in segs} == {a["id"]: "face+vestuário", b["id"]: "vestuário"}
    body_dets = client.get(f"/api/jobs/{job['id']}/detections", params={"kind": "body"}).json()
    assert body_dets and all(d["reference_id"] is None and d["colors"] for d in body_dets)
    # Frame original de uma deteção de corpo (recorte com outra margem) e recusa como referência facial.
    r = client.get(f"/api/detections/{body_dets[0]['id']}/frame/original")
    assert r.status_code == 200 and r.headers["x-faceai-extraction"] == "seek"
    assert client.post(f"/api/detections/{body_dets[0]['id']}/as-reference").status_code == 422

    # Relatório e exportações
    html = client.get(f"/api/jobs/{job['id']}/export/html").text
    assert "Correspondências por vestuário" in html and "evidência complementar" in html and "laranja" in html
    data = client.get(f"/api/jobs/{job['id']}/export/json").json()
    assert {d["kind"] for d in data["detections"]} == {"face", "body"} and data["job"]["clothing_samples"] == 1
    assert client.get(f"/api/jobs/{job['id']}/export/pdf").content.startswith(b"%PDF")


def test_clothing_without_face_match_has_no_samples(client, tmp_path, fake_body):
    case = client.post("/api/cases", json={"name": "Loja"}).json()
    with make_image(tmp_path / "ref.jpg", RED).open("rb") as fh:
        client.post(f"/api/cases/{case['id']}/references", files=[("files", ("ref.jpg", fh, "image/jpeg"))])
    _upload_video(client, case["id"], make_people_video(tmp_path / "camB.mp4", [
        {"x": 40, "start": 0, "end": 6, "torso": ORANGE, "legs": NAVY, "head": None}]))
    params = {"sample_fps": 2, "threshold": 0.05, "motion_filter": False, "clothing": True, "min_body_height": 40}
    job = wait_for_job(client, client.post(f"/api/cases/{case['id']}/jobs", json={"params": params}).json()["id"])
    assert job["status"] == "completed" and job["clothing_samples"] == 0
    assert "Sem amostras de vestuário" in job["message"]
    assert client.get(f"/api/jobs/{job['id']}/tracks", params={"kind": "body"}).json() == []


def test_yolox_decode_without_model():
    """Saída bruta do YOLOX (grelhas e strides) → caixa na imagem original (com letterbox) e NMS."""
    from app.services.body_engine import YoloxPersonDetector, _yolox_grids

    det = YoloxPersonDetector.__new__(YoloxPersonDetector)
    det.size, det.input_name = 640, "images"
    det.grids, det.strides = _yolox_grids(640)
    out = np.zeros((1, 8400, 85), np.float32)
    out[..., 2:4] = -5  # caixas minúsculas e score 0 por omissão
    i = 40 * 80 + 20  # stride 8: célula (x=20, y=40)
    out[0, i, :4] = [0.5, 0.5, np.log(100 / 8), np.log(200 / 8)]  # centro (164, 324), 100×200 no espaço 640
    out[0, i, 4], out[0, i, 5] = 0.9, 0.95
    out[0, i + 1] = out[0, i]  # duplicado na célula vizinha: o NMS fica só com um
    out[0, i + 1, 0] = -0.5

    class Session:
        def run(self, _names, feeds):
            assert feeds["images"].shape == (1, 3, 640, 640)
            return [out]

    det.session = Session()
    boxes = det.detect(np.zeros((960, 1280, 3), np.uint8))  # letterbox: escala 0,5
    assert len(boxes) == 1
    x1, y1, x2, y2, score = boxes[0]
    assert (round(x1), round(y1), round(x2), round(y2)) == (228, 448, 428, 848) and score == pytest.approx(0.855)
