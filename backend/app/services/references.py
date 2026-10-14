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


def person_groups(refs: list[ReferenceImage], names: dict[int, str] | None = None) -> list[dict]:
    """Agrupa as referências pela pessoa do caso (CasePerson), pela ordem em que aparecem; as fotos soltas formam
    um grupo próprio. [{"id": 1, "label": "Suspeito A", "case_person_id": 3, "reference_ids": [3, 5]}, ...]."""
    names = names or {}
    order: list[int | None] = []
    groups: dict[int | None, list[int]] = {}
    for r in refs:
        key = r.case_person_id
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(r.id)
    single = len(order) == 1
    out = []
    for i, key in enumerate(order):
        if key is None:
            label = "Pessoa" if single else "Fotos soltas"
        else:
            label = names.get(key) or f"Pessoa {key}"
        out.append({"id": i + 1, "label": label, "case_person_id": key, "reference_ids": groups[key]})
    return out


def migrate_person_labels(s) -> int:
    """Versão anterior: o rótulo de texto (ReferenceImage.person_label) definia a pessoa. Converte cada rótulo
    distinto, por caso, numa CasePerson (uma vez; o rótulo fica como estava). Devolve o n.º de pessoas criadas."""
    from sqlmodel import select

    from ..models import CasePerson

    created = 0
    pending = s.exec(select(ReferenceImage).where(ReferenceImage.case_person_id == None,  # noqa: E711
                                                  ReferenceImage.person_label != "")).all()
    by_key: dict[tuple[int, str], CasePerson] = {}
    for r in pending:
        key = (r.case_id, r.person_label.strip())
        if key not in by_key:
            existing = s.exec(select(CasePerson).where(CasePerson.case_id == r.case_id,
                                                       CasePerson.name == key[1])).first()
            if existing is None:
                existing = CasePerson(case_id=r.case_id, name=key[1], created_by="migração")
                s.add(existing)
                s.flush()
                created += 1
            by_key[key] = existing
        r.case_person_id = by_key[key].id
        s.add(r)
    s.commit()
    return created
