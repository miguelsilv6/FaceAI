"""Bases de dados criadas por versões anteriores recebem as colunas novas sem perder dados."""

import sqlite3

from sqlalchemy import create_engine, inspect

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
    monkeypatch.setattr(db, "_engine", None)
