import numpy as np

from app.services import face_engine


def test_no_detection_placeholder_is_ignored(monkeypatch):
    """Sem faces, o DeepFace devolve a imagem inteira (w = largura - 1) com confiança 0: não é uma face."""
    import deepface.DeepFace as df

    def fake_represent(img_path, **_):
        h, w = img_path.shape[:2]
        return [
            {"facial_area": {"x": 0, "y": 0, "w": w - 1, "h": h - 1}, "face_confidence": 0.0, "embedding": [1.0, 0.0]},
            {"facial_area": {"x": 10, "y": 20, "w": 30, "h": 40}, "face_confidence": 0.98, "embedding": [0.0, 1.0]},
        ]

    monkeypatch.setattr(df, "represent", fake_represent)
    faces = face_engine.DeepFaceEngine().detect(np.zeros((200, 300, 3), np.uint8), model="ArcFace", detector="x")
    assert [(f.x, f.y, f.w, f.h) for f in faces] == [(10, 20, 30, 40)]


def test_upscale_maps_boxes_back(monkeypatch):
    import deepface.DeepFace as df

    monkeypatch.setattr(df, "represent", lambda img_path, **_: [
        {"facial_area": {"x": 20, "y": 40, "w": 60, "h": 80}, "face_confidence": 0.9, "embedding": [1.0]}
    ])
    faces = face_engine.DeepFaceEngine().detect(np.zeros((100, 100, 3), np.uint8), model="ArcFace", detector="x",
                                                upscale=2.0)
    assert (faces[0].x, faces[0].y, faces[0].w, faces[0].h) == (10, 20, 30, 40)
