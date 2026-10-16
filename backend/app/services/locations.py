"""Locais de um caso: cada local agrupa os vídeos (câmaras/ângulos) desse local."""

from __future__ import annotations

from sqlmodel import select

from ..models import Location, Video

DEFAULT_LOCATION = "Geral"


def camera_label(v: Video) -> str:
    """Nome da câmara (por omissão, o nome do ficheiro)."""
    return (v.camera_name or "").strip() or v.filename


def default_location(s, case_id: int, actor: str = "") -> Location:
    """Local "Geral" do caso (criado se ainda não existir)."""
    loc = s.exec(select(Location).where(Location.case_id == case_id, Location.name == DEFAULT_LOCATION)).first()
    if loc is None:
        loc = Location(case_id=case_id, name=DEFAULT_LOCATION, created_by=actor)
        s.add(loc)
        s.flush()
    return loc


def locations_by_id(s, case_id: int) -> dict[int, Location]:
    return {loc.id: loc for loc in s.exec(select(Location).where(Location.case_id == case_id)).all()}


def video_place(v: Video, locs: dict[int, Location]) -> dict:
    """Campos de identificação de um vídeo nas respostas: local, câmara e o rótulo "Local · Câmara"."""
    loc = locs.get(v.location_id) if v.location_id is not None else None
    name = loc.name if loc else ""
    cam = camera_label(v)
    return {"location_id": v.location_id, "location_name": name, "camera_name": cam,
            "camera_label": f"{name} · {cam}" if name else cam}


def migrate_locations(s) -> int:
    """Versão anterior: os vídeos pertenciam diretamente ao caso. Atribui os vídeos sem local ao local "Geral"
    do respetivo caso (criado se preciso). Idempotente. Devolve o n.º de vídeos atribuídos."""
    orphans = s.exec(select(Video).where(Video.location_id == None)).all()  # noqa: E711
    by_case: dict[int, Location] = {}
    for v in orphans:
        if v.case_id not in by_case:
            by_case[v.case_id] = default_location(s, v.case_id, "migração")
        v.location_id = by_case[v.case_id].id
        s.add(v)
    s.commit()
    return len(orphans)
