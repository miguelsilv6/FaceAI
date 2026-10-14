"""Autenticação, sessões, CSRF/origem e autorização por caso (incluindo uma matriz automática sobre todas as rotas)."""

from __future__ import annotations

import re
from datetime import timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from .conftest import ADMIN, ADMIN_PASSWORD, create_user, login, new_client, totp_code

PW = "girassol-trovoada-marmelo-9"


def _db():
    import app.db

    return Session(app.db.get_engine())


def _user(username: str):
    from app.models import User

    with _db() as s:
        return s.exec(select(User).where(User.username == username)).one()


def _anon() -> TestClient:
    import app.main

    return TestClient(app.main.app)


# ----------------------------------------------------------------------------- serviços
def test_password_policy():
    from app.services.auth import password_problem

    assert password_problem("curta") is not None
    assert password_problem("password1234") is not None  # lista de passwords comuns
    assert password_problem("joao.silva-muito-longa", "joao.silva") is not None
    assert password_problem("aaaaaaaaaaaaaaaa") is not None
    assert password_problem("faceai-e-seguro-2024") is not None
    assert password_problem("girassol-trovoada-marmelo") is None


def test_totp_window_and_replay(client):
    from app.models import User
    from app.services import auth

    secret = pyotp.random_base32(32)
    u = User(username="x", password_hash="", totp_secret_enc=auth.encrypt(secret))
    t = 1_700_000_000.0
    totp = pyotp.TOTP(secret)
    assert auth.verify_totp(u, totp.at(t), at=t)
    assert not auth.verify_totp(u, totp.at(t), at=t)  # replay recusado
    assert auth.verify_totp(u, totp.at(t + 30), at=t)  # passo seguinte (relógio adiantado) aceite
    assert not auth.verify_totp(u, totp.at(t - 30), at=t)  # passo anterior ao já usado
    assert not auth.verify_totp(u, totp.at(t + 90), at=t)  # fora da janela
    assert not auth.verify_totp(u, "12345a", at=t)
    assert auth.decrypt(u.totp_secret_enc) == secret and secret not in u.totp_secret_enc


def test_server_key_file_permissions(client):
    import os

    from app.config import settings

    mode = os.stat(settings.data_dir / "secret.key").st_mode & 0o777
    assert mode == 0o600


# ----------------------------------------------------------------------------- login
def test_setup_gate(client, monkeypatch):
    import app.main
    from app.models import User

    with _db() as s:
        for u in s.exec(select(User)):
            u.active = False
            s.add(u)
        s.commit()
    monkeypatch.setitem(app.main._setup_state, "done", False)
    anon = _anon()
    assert anon.get("/api/auth/status").json() == {"setup_required": True}
    r = anon.post("/api/auth/login", json={"username": "a", "password": "b"})
    assert r.status_code == 503 and "create-admin" in r.json()["detail"]


def test_unauthenticated_is_rejected(client):
    anon = _anon()
    assert anon.get("/api/auth/status").json() == {"setup_required": False}
    assert anon.get("/api/cases").status_code == 401
    assert anon.get("/api/system").status_code == 401
    assert anon.get("/api/auth/me").status_code == 401


def test_login_cookie_flags_and_me(client):
    anon = _anon()
    create_user("ana", PW)
    r = anon.post("/api/auth/login", json={"username": "ANA ", "password": PW})
    assert r.status_code == 200 and r.json()["stage"] == "mfa_setup"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict") and "Path=/" in cookie
    # Etapa intermédia: ainda sem acesso aos dados
    assert anon.get("/api/cases").status_code == 401
    me = client.get("/api/auth/me").json()
    assert me["stage"] == "ok" and me["user"]["username"] == ADMIN and me["user"]["role"] == "admin"
    assert "password_hash" not in me["user"] and "totp_secret_enc" not in str(me)


def test_wrong_password_generic_and_lockout(client):
    create_user("bruno", PW)
    anon = _anon()
    r1 = anon.post("/api/auth/login", json={"username": "bruno", "password": "errada-errada-1"})
    r2 = anon.post("/api/auth/login", json={"username": "nao-existe", "password": "errada-errada-1"})
    assert r1.status_code == r2.status_code == 401 and r1.json() == r2.json()
    for _ in range(4):
        anon.post("/api/auth/login", json={"username": "bruno", "password": "errada-errada-1"})
    assert _user("bruno").locked_until is not None
    # Bloqueada: nem a password certa entra, e a mensagem é a mesma
    r = anon.post("/api/auth/login", json={"username": "bruno", "password": PW})
    assert r.status_code == 401 and r.json() == r1.json()
    events = [e["action"] for e in client.get("/api/audit/system").json()]
    assert "auth.account_locked" in events and "auth.login_failed" in events
    # O admin desbloqueia
    assert client.post(f"/api/users/{_user('bruno').id}/unlock").status_code == 200
    assert anon.post("/api/auth/login", json={"username": "bruno", "password": PW}).status_code == 200


def test_ip_limit(client, monkeypatch):
    from app.services import auth

    monkeypatch.setattr(auth, "IP_MAX_FAILURES", 3)
    anon = _anon()
    for i in range(3):
        anon.post("/api/auth/login", json={"username": f"u{i}", "password": "errada-errada-1"})
    assert anon.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PASSWORD}).status_code == 429


def test_mfa_required_and_recovery_codes(client):
    c = _anon()
    create_user("carla", PW)
    totp = login(c, "carla", PW)
    c.post("/api/auth/logout")
    # Segundo login: etapa "mfa"; código errado recusado; código de recuperação aceite uma única vez
    r = c.post("/api/auth/login", json={"username": "carla", "password": PW})
    assert r.json()["stage"] == "mfa"
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c.post("/api/auth/mfa/setup").status_code == 401  # o MFA já está configurado
    assert c.post("/api/auth/mfa/verify", json={"code": "000000"}).status_code == 400
    codes = c.post("/api/auth/mfa/recovery-codes", json={"password": PW})
    assert codes.status_code == 401  # ainda não autenticado
    c2 = _anon()
    t = login(c2, "carla", PW, totp)
    recovery = c2.post("/api/auth/mfa/recovery-codes", json={"password": PW}).json()["recovery_codes"]
    assert len(recovery) == 10 and t
    r = c.post("/api/auth/mfa/verify", json={"code": recovery[0]})
    assert r.status_code == 200 and r.json()["stage"] == "ok"
    c.post("/api/auth/logout")
    r = c.post("/api/auth/login", json={"username": "carla", "password": PW})
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c.post("/api/auth/mfa/verify", json={"code": recovery[0]}).status_code == 400  # uso único
    assert c.post("/api/auth/mfa/verify", json={"code": totp_code(totp, "carla")}).status_code == 200


def test_session_token_rotates_and_logout(client):
    c = _anon()
    create_user("diana", PW)
    r = c.post("/api/auth/login", json={"username": "diana", "password": PW})
    first = c.cookies.get("faceai_session")
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    secret = c.post("/api/auth/mfa/setup").json()["secret"]
    r = c.post("/api/auth/mfa/verify", json={"code": totp_code(pyotp.TOTP(secret), "diana")})
    assert len(r.json()["recovery_codes"]) == 10
    second = c.cookies.get("faceai_session")
    assert first and second and first != second
    old = _anon()
    old.cookies.set("faceai_session", first)
    assert old.get("/api/auth/me").status_code == 401  # o token antigo deixou de valer
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c.get("/api/cases").status_code == 200
    assert c.post("/api/auth/logout").status_code == 204
    stolen = _anon()
    stolen.cookies.set("faceai_session", second)
    assert stolen.get("/api/cases").status_code == 401


def test_session_idle_and_max_expiry(client):
    from app.models import UserSession
    from app.services import auth

    for field, delta in (("last_seen_at", timedelta(minutes=31)), ("expires_at", timedelta(hours=13))):
        c = new_client(f"exp{field[:4]}", PW)
        assert c.get("/api/cases").status_code == 200
        with _db() as s:
            for sess in s.exec(select(UserSession).where(UserSession.user_id == _user(f"exp{field[:4]}").id)):
                setattr(sess, field, getattr(sess, field) - delta)
                s.add(sess)
            s.commit()
        assert c.get("/api/cases").status_code == 401
    assert auth.PENDING_SESSION_MINUTES <= 10


def test_session_stored_hashed(client):
    from app.models import UserSession

    token = client.cookies.get("faceai_session")
    with _db() as s:
        ids = [x.id for x in s.exec(select(UserSession))]
    assert token not in ids and len(ids[0]) == 64


def test_forced_password_change(client):
    c = _anon()
    create_user("eva", PW, must_change=True)
    login(c, "eva", PW)
    assert c.stage == "password"
    assert c.get("/api/cases").status_code == 401
    r = c.post("/api/auth/password", json={"current_password": PW, "new_password": "curta"})
    assert r.status_code == 422
    r = c.post("/api/auth/password", json={"current_password": "errada-errada-1", "new_password": PW + "x"})
    assert r.status_code == 400
    r = c.post("/api/auth/password", json={"current_password": PW, "new_password": "nova-password-bem-longa"})
    assert r.status_code == 200 and r.json()["stage"] == "ok"
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c.get("/api/cases").status_code == 200


def test_csrf_and_origin(client):
    token = client.headers.pop("X-CSRF-Token")
    r = client.post("/api/cases", json={"name": "Sem CSRF"})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
    r = client.post("/api/cases", json={"name": "CSRF errado"}, headers={"X-CSRF-Token": "x" * 43})
    assert r.status_code == 403
    client.headers["X-CSRF-Token"] = token
    r = client.post("/api/cases", json={"name": "Origem má"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/cases", json={"name": "Referer mau"}, headers={"Referer": "https://evil.example/x"})
    assert r.status_code == 403
    r = client.post("/api/cases", json={"name": "Mesma origem"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 201


def test_security_headers(client):
    r = client.get("/api/cases")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "same-origin"
    assert client.get("/api/openapi.json").status_code in (401, 404)
    assert client.get("/docs").status_code == 404


def test_actor_is_authenticated_user_not_header(client):
    case = client.post("/api/cases", json={"name": "C"}, headers={"X-Analyst": "Outra%20Pessoa"}).json()
    events = client.get(f"/api/cases/{case['id']}/audit").json()
    assert events[0]["actor"] == ADMIN and events[0]["ip"] == "testclient"


# ----------------------------------------------------------------------------- utilizadores (admin)
def test_user_admin_and_last_admin(client):
    r = client.post("/api/users", json={"username": "Mau Nome", "password": PW})
    assert r.status_code == 422
    r = client.post("/api/users", json={"username": "fabio", "password": "password1234"})
    assert r.status_code == 422
    r = client.post("/api/users", json={"username": "fabio", "display_name": "Fábio", "password": PW})
    assert r.status_code == 201 and r.json()["must_change_password"] is True
    assert client.post("/api/users", json={"username": "fabio", "password": PW}).status_code == 409
    me = _user(ADMIN)
    assert client.patch(f"/api/users/{me.id}", json={"role": "analyst"}).status_code == 409
    assert client.patch(f"/api/users/{me.id}", json={"active": False}).status_code == 409
    fabio = r.json()["id"]
    assert client.patch(f"/api/users/{fabio}", json={"role": "admin"}).json()["role"] == "admin"
    assert client.patch(f"/api/users/{fabio}", json={"active": False}).json()["active"] is False
    # Um analista não gere utilizadores
    c = new_client("gil", PW)
    assert c.get("/api/users").status_code == 403
    assert c.post("/api/users", json={"username": "hacker", "password": PW}).status_code == 403
    assert c.get("/api/audit/system").status_code == 403
    # Desativar termina as sessões
    assert client.patch(f"/api/users/{_user('gil').id}", json={"active": False}).status_code == 200
    assert c.get("/api/cases").status_code == 401


def test_reset_mfa_and_password_end_sessions(client):
    c = new_client("hugo", PW)
    uid = _user("hugo").id
    assert client.post(f"/api/users/{uid}/reset-mfa").json()["totp_enabled"] is False
    assert c.get("/api/cases").status_code == 401
    c2 = _anon()
    login(c2, "hugo", PW)  # volta a configurar o MFA
    assert client.post(f"/api/users/{uid}/reset-password", json={"password": "temporaria-bem-longa"}).status_code == 200
    assert c2.get("/api/cases").status_code == 401


# ----------------------------------------------------------------------------- autorização por caso
def _seed_case(owner: TestClient) -> dict:
    """Caso com um objeto de cada tipo (inseridos diretamente na base de dados)."""
    from app.models import Detection, Job, ReferenceImage, Track, Upload, Video

    case = owner.post("/api/cases", json={"name": "Caso privado"}).json()
    with _db() as s:
        video = Video(case_id=case["id"], filename="v.mp4", stored_path="/nao/existe.mp4", size_bytes=1)
        ref = ReferenceImage(case_id=case["id"], filename="r.jpg", stored_path="/nao/existe.jpg", size_bytes=1)
        job = Job(case_id=case["id"], created_by="x")
        up = Upload(id="u" * 32, case_id=case["id"], kind="video", filename="v.mp4", size_bytes=10,
                    temp_path="/nao/existe")
        s.add_all([video, ref, job, up])
        s.commit()
        det = Detection(job_id=job.id, video_id=video.id, frame_index=0, timestamp_s=0, x=0, y=0, w=1, h=1,
                        face_confidence=1, distance=0, similarity=1, is_match=True, crop_path="", frame_path="")
        track = Track(job_id=job.id, video_id=video.id, start_s=0, end_s=1)
        s.add_all([det, track])
        s.commit()
        return {"case_id": case["id"], "video_id": video.id, "ref_id": ref.id, "job_id": job.id,
                "det_id": det.id, "track_id": track.id, "upload_id": up.id}


OBJECT_PARAMS = {"case_id", "video_id", "ref_id", "job_id", "det_id", "track_id", "upload_id"}
PUBLIC = {"/api/auth/status", "/api/auth/login"}


def _api_routes():
    """Todas as rotas da API, a partir do esquema OpenAPI (inclui as que forem acrescentadas no futuro)."""
    import app.main

    app.main.app.openapi_schema = None
    for path, ops in app.main.app.openapi()["paths"].items():
        if path.startswith("/api/"):
            for method in ops:
                yield method.upper(), path


def _fill(path: str, ids: dict) -> str:
    return re.sub(r"\{(\w+)(:[^}]*)?\}", lambda m: str(ids.get(m.group(1), 999999)), path)


def test_every_route_requires_authentication(client):
    anon = _anon()
    routes = list(_api_routes())
    assert len(routes) > 40
    for method, path in routes:
        if path in PUBLIC:
            continue
        r = anon.request(method, _fill(path, {}), json={})
        # As rotas de /api/auth validam o corpo antes de ler a sessão (422 não revela nada).
        allowed = (401, 403, 422) if path.startswith("/api/auth/") else (401, 403)
        assert r.status_code in allowed, f"{method} {path} → {r.status_code}"


def test_authorization_matrix_outsider_gets_404(client):
    """Um analista sem acesso ao caso não chega a nenhum objeto dele, por nenhuma rota (atual ou futura)."""
    ids = _seed_case(client)
    outsider = new_client("intruso", PW)
    checked = 0
    for method, path in _api_routes():
        params = set(re.findall(r"\{(\w+)", path))
        if not params & OBJECT_PARAMS:
            continue
        r = outsider.request(method, _fill(path, ids), json={})
        assert r.status_code == 404, f"{method} {path} → {r.status_code} {r.text[:200]}"
        checked += 1
    assert checked > 30
    assert outsider.get("/api/cases").json() == []


def test_member_owner_and_admin_rights(client):
    ids = _seed_case(client)  # o admin é o responsável (owner)
    ana = new_client("ana.m", PW)
    rui = new_client("rui.m", PW)
    cid = ids["case_id"]
    assert ana.get(f"/api/cases/{cid}").status_code == 404
    r = client.post(f"/api/cases/{cid}/members", json={"username": "ana.m", "role": "member"})
    assert r.status_code == 201
    assert ana.get(f"/api/cases/{cid}").status_code == 200
    assert [c["id"] for c in ana.get("/api/cases").json()] == [cid]
    # Membro: trabalha no caso mas não gere membros, não edita nem apaga o caso
    assert ana.post(f"/api/cases/{cid}/members", json={"username": "rui.m"}).status_code == 403
    assert ana.patch(f"/api/cases/{cid}", json={"name": "Novo"}).status_code == 403
    assert ana.request("DELETE", f"/api/cases/{cid}", json={"confirm_case_id": cid}).status_code == 403
    jid = ids["job_id"]
    assert ana.request("DELETE", f"/api/jobs/{jid}", json={"confirm_job_id": jid}).status_code == 403
    # Rui continua sem acesso; o próprio caso de Ana não é visível ao Rui
    assert rui.get(f"/api/cases/{cid}").status_code == 404
    own = ana.post("/api/cases", json={"name": "Da Ana"}).json()
    assert rui.get(f"/api/cases/{own['id']}").status_code == 404
    assert {c["id"] for c in client.get("/api/cases").json()} >= {cid, own["id"]}  # admin vê todos
    # Owner promove; não se pode remover o último owner
    owner_id = _user(ADMIN).id
    client.post(f"/api/cases/{cid}/members", json={"username": "ana.m", "role": "owner"})
    assert ana.patch(f"/api/cases/{cid}", json={"name": "Renomeado"}).status_code == 200
    assert ana.request("DELETE", f"/api/cases/{cid}/members/{owner_id}").status_code == 204
    assert ana.request("DELETE", f"/api/cases/{cid}/members/{_user('ana.m').id}").status_code == 409
    actions = [e["action"] for e in client.get(f"/api/cases/{cid}/audit").json()]
    assert "case.member_added" in actions and "case.member_removed" in actions


@pytest.mark.parametrize("stage_user", ["sem-mfa"])
def test_pending_stage_cannot_reach_data(client, stage_user):
    c = _anon()
    create_user(stage_user, PW)
    r = c.post("/api/auth/login", json={"username": stage_user, "password": PW})
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c.get("/api/cases").status_code == 401
    assert c.post("/api/cases", json={"name": "x"}).status_code == 401
    assert c.get("/api/auth/sessions").status_code == 401


def test_sessions_list_and_end(client):
    other = _anon()
    login(other, ADMIN, ADMIN_PASSWORD, client.totp)
    rows = client.get("/api/auth/sessions").json()
    assert len(rows) == 2 and sum(r["current"] for r in rows) == 1
    target = next(r for r in rows if not r["current"])
    assert client.delete(f"/api/auth/sessions/{target['id']}").status_code == 204
    assert other.get("/api/cases").status_code == 401
    assert client.get("/api/cases").status_code == 200
