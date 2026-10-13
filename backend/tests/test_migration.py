"""Bases de dados criadas por versões anteriores recebem as colunas novas sem perder dados."""

import sqlite3

from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect

from app import db


def test_add_missing_columns(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE detection (id INTEGER PRIMARY KEY, job_id INTEGER, distance FLOAT)")
    con.execute("INSERT INTO detection (id, job_id, distance) VALUES (1, 1, 0.3)")
    con.commit()
    con.close()
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(db, "_engine", engine)
    db.init_db()
    cols = {c["name"] for c in inspect(engine).get_columns("detection")}
    assert {"track_id", "quality", "sharpness", "size_score", "frontal", "review_status"} <= cols
    with engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT id, distance, quality, review_status FROM detection").one()
    assert tuple(row) == (1, 0.3, 0.0, "pending")
    with engine.connect() as conn:  # colunas do vestuário: dados antigos ficam como faciais
        assert conn.exec_driver_sql("SELECT kind FROM detection").scalar() == "face"
    assert "ix_detection_track_id" in {i["name"] for i in inspect(engine).get_indexes("detection")}
    monkeypatch.setattr(db, "_engine", None)


def test_reference_id_becomes_nullable(tmp_path, monkeypatch):
    """Bases antigas tinham detection.reference_id NOT NULL; o modo descoberta grava deteções sem referência."""
    path = tmp_path / "old2.db"
    from sqlalchemy.schema import CreateTable
    from sqlmodel import SQLModel

    from app import models  # noqa: F401

    table = SQLModel.metadata.tables["detection"]
    ddl = str(CreateTable(table).compile(dialect=sqlite_dialect()))
    assert "reference_id INTEGER," in ddl
    con = sqlite3.connect(path)
    con.execute(ddl.replace("reference_id INTEGER,", "reference_id INTEGER NOT NULL,").replace(
        "FOREIGN KEY", "-- ").split("-- ")[0].rstrip().rstrip(",") + ")")
    con.execute("CREATE INDEX ix_detection_track_id ON detection (track_id)")
    con.execute("INSERT INTO detection (id, job_id, video_id, reference_id, frame_index, timestamp_s, x, y, w, h, "
                "face_confidence, distance, similarity, is_match, crop_path, frame_path, quality, sharpness, "
                "size_score, review_status, review_note, reviewed_by, track_id, kind) "
                "VALUES (7, 1, 1, 3, 0, 0, 0, 0, 1, 1, 1, 0.3, 0.7, 1, '', '', 0, 0, 0, 'pending', '', '', 2, 'face')")
    con.commit()
    con.close()
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(db, "_engine", engine)
    db.init_db()
    cols = {c["name"]: c for c in inspect(engine).get_columns("detection")}
    assert cols["reference_id"]["nullable"]
    with engine.begin() as conn:
        assert tuple(conn.exec_driver_sql("SELECT id, reference_id, distance, track_id FROM detection").one()) == (
            7, 3, 0.3, 2)
        conn.exec_driver_sql("INSERT INTO detection (job_id, video_id, reference_id, frame_index, timestamp_s, x, y, "
                             "w, h, face_confidence, distance, similarity, is_match, crop_path, frame_path, quality, "
                             "sharpness, size_score, review_status, review_note, reviewed_by, kind) "
                             "VALUES (1, 1, NULL, 0, 0, 0, 0, 1, 1, 1, 0, 1, 0, '', '', 0, 0, 0, 'pending', '', '', "
                             "'face')")
    names = {i["name"] for i in inspect(engine).get_indexes("detection")}
    assert {"ix_detection_track_id", "ix_detection_job_id"} <= names
    assert not inspect(engine).has_table("_old_detection")
    db.init_db()  # idempotente
    monkeypatch.setattr(db, "_engine", None)
