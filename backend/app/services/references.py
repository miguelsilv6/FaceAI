"""Validação de imagens de referência e cálculo dos respetivos embeddings."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..models import ReferenceImage
from .face_engine import FaceEngine, FaceObservation
from .imageio import read_image, write_jpeg
from .video_processor import crop_with_margin


def largest_face(faces: list[FaceObservation]) -> FaceObservation | None:
    return max(faces, key=lambda f: f.w * f.h, default=None)


def validate_reference(
    ref: ReferenceImage, engine: FaceEngine, *, model: str, detector: str, crop_dir: Path
) -> ReferenceImage:
    """Deteta faces na imagem de referência; é válida só se tiver exatamente uma face."""
    try:
        img = read_image(ref.stored_path)
    except ValueError as exc:
        ref.valid, ref.message = False, str(exc)
        return ref
    ref.height, ref.width = img.shape[:2]
    faces = engine.detect(img, model=model, detector=detector)
    ref.face_count = len(faces)
    if not faces:
        ref.valid, ref.message = False, "Nenhuma face detetada na imagem."
        return ref
    face = largest_face(faces)
    crop_path = crop_dir / f"ref_{ref.id:05d}_face.jpg"
    write_jpeg(crop_path, crop_with_margin(img, face.x, face.y, face.w, face.h, margin=0.15))
    ref.face_crop_path = str(crop_path)
    if len(faces) > 1:
        ref.valid = False
        ref.message = (
            f"Foram detetadas {len(faces)} faces. Use uma imagem recortada apenas com a pessoa de interesse."
        )
    else:
        ref.valid, ref.message = True, f"Face detetada (confiança {face.confidence:.2f})."
    return ref


def reference_embeddings(
    refs: list[ReferenceImage], engine: FaceEngine, *, model: str, detector: str
) -> tuple[list[ReferenceImage], np.ndarray, list[str]]:
    """Embeddings das referências válidas com o modelo/detetor da análise."""
    used: list[ReferenceImage] = []
    vectors: list[np.ndarray] = []
    warnings: list[str] = []
    for ref in refs:
        if not ref.valid:
            warnings.append(f"Referência '{ref.filename}' ignorada: {ref.message}")
            continue
        faces = engine.detect(read_image(ref.stored_path), model=model, detector=detector)
        face = largest_face(faces)
        if face is None:
            warnings.append(f"Referência '{ref.filename}' ignorada: sem face com o detetor '{detector}'.")
            continue
        used.append(ref)
        vectors.append(face.embedding)
    matrix = np.stack(vectors) if vectors else np.zeros((0, 0), dtype=np.float32)
    return used, matrix, warnings


def person_groups(refs: list[ReferenceImage]) -> list[dict]:
    """Agrupa as referências por pessoa (rótulo), pela ordem em que os rótulos aparecem.

    [{"id": 1, "label": "Suspeito A", "reference_ids": [3, 5]}, ...]. Sem rótulos: uma só pessoa."""
    order: list[str] = []
    groups: dict[str, list[int]] = {}
    for r in refs:
        label = (r.person_label or "").strip()
        if label not in groups:
            order.append(label)
            groups[label] = []
        groups[label].append(r.id)
    single = len(order) == 1
    return [{"id": i + 1, "label": lbl or ("Pessoa" if single else "Sem rótulo"), "reference_ids": groups[lbl]}
            for i, lbl in enumerate(order)]
