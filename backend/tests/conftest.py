from __future__ import annotations

import importlib
import time

import pyotp
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from .fakes import FakeEngine


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FACEAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FACEAI_FRONTEND_DIST", str(tmp_path / "no-frontend"))
    monkeypatch.setenv("FACEAI_PRELOAD_MODELS", "0")
    # Recarrega a configuração e a base de dados para usar a pasta temporária.
    import app.config
    import app.db

    importlib.reload(app.config)
    app.db.settings = app.config.settings
    app.db._engine = None
    from app.services import audit, disk, face_engine, jobs, reports, transcode

    monkeypatch.setattr(transcode, "ffmpeg_available", lambda: False)

    for mod in (audit, disk, jobs, reports):
        mod.settings = app.config.settings
    import app.api.cases as cases_api
    import app.api.deps as deps
    import app.main
    from app.services import auth

    cases_api.settings = app.config.settings
    app.main.settings = app.config.settings
    deps.settings = app.config.settings
    auth.settings = app.config.settings
    auth._server_key.cache_clear()
    # Argon2 barato nos testes (os parâmetros reais demoram ~0,1 s por verificação).
    monkeypatch.setattr(auth, "hasher", PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1))
    monkeypatch.setattr(auth, "_DUMMY_HASH", auth.hasher.hash("dummy-password-x"))
    monkeypatch.setitem(app.main._setup_state, "done", False)
    monkeypatch.setitem(disk._last_alert, "level", "ok")
    face_engine.set_engine(FakeEngine())
    with TestClient(app.main.app) as c:
        create_user(ADMIN, ADMIN_PASSWORD, role="admin", must_change=False)
        c.totp = login(c, ADMIN, ADMIN_PASSWORD, mfa=True)
        yield c
    face_engine.set_engine(None)
    app.db._engine = None


ADMIN = "admin.teste"
ADMIN_PASSWORD = "cavalo-bateria-agrafo-correto"


def create_user(username: str, password: str, *, role: str = "analyst", must_change: bool = False,
                display_name: str = "") -> int:
    """Cria um utilizador diretamente na base de dados (sem MFA)."""
    from sqlmodel import Session

    import app.db
    from app.models import User
    from app.services import auth

    with Session(app.db.get_engine()) as s:
        u = User(username=username, display_name=display_name or username, role=role,
                 password_hash=auth.hash_password(password), must_change_password=must_change)
        s.add(u)
        s.commit()
        return u.id


def totp_code(totp: pyotp.TOTP, username: str) -> str:
    """Código TOTP ainda não usado por este utilizador (o servidor recusa passos repetidos)."""
    from sqlmodel import Session, select

    import app.db
    from app.models import User

    with Session(app.db.get_engine()) as s:
        last = s.exec(select(User).where(User.username == username)).one().totp_last_step
    while True:
        now = int(time.time() // 30)
        step = max(now - 1, last + 1)
        if step <= now + 1:
            return totp.at(step * 30)
        time.sleep(1)


def login(c: TestClient, username: str, password: str, totp: pyotp.TOTP | None = None, *,
          mfa: bool = False) -> pyotp.TOTP | None:
    """Login completo e cabeçalho CSRF no cliente. Passa o MFA se estiver ativo (ou for obrigatório) e, com
    mfa=True, ativa-o em "A minha conta" se ainda não estiver. Devolve o gerador TOTP do utilizador (ou None)."""
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    if r.json()["stage"] in ("mfa_setup", "mfa"):
        if r.json()["stage"] == "mfa_setup":
            totp = pyotp.TOTP(c.post("/api/auth/mfa/setup").json()["secret"])
        assert totp is not None
        r = c.post("/api/auth/mfa/verify", json={"code": totp_code(totp, username)})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    c.stage = r.json()["stage"]
    if mfa and totp is None:
        totp = enable_mfa(c, username)
    return totp


def enable_mfa(c: TestClient, username: str) -> pyotp.TOTP:
    """Ativa o MFA a partir de uma sessão completa (como em "A minha conta")."""
    totp = pyotp.TOTP(c.post("/api/auth/mfa/setup").json()["secret"])
    r = c.post("/api/auth/mfa/enable", json={"code": totp_code(totp, username)})
    assert r.status_code == 200, r.text
    c.recovery_codes = r.json()["recovery_codes"]
    return totp


def new_client(username: str, password: str, *, role: str = "analyst") -> TestClient:
    """Outro utilizador, com sessão própria (cookies separados) na mesma aplicação."""
    import app.main

    create_user(username, password, role=role)
    c = TestClient(app.main.app)
    c.totp = login(c, username, password)
    return c


def wait_for_job(client, job_id: int, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id} não terminou")
