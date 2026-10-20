"""Login em etapas (password → MFA TOTP → mudança de password obrigatória), sessão, logout e conta própria.

Etapas da sessão (UserSession.stage):
- "mfa_setup": password certa, MFA por configurar (só com FACEAI_REQUIRE_MFA=true);
- "mfa": password certa, falta o código TOTP (ou um código de recuperação) — utilizadores com MFA ativo;
- "password": autenticado, mas tem de mudar a password temporária antes de continuar;
- "ok": sessão completa.
O token da sessão é renovado em cada mudança de etapa (proteção contra fixação de sessão).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..config import settings
from ..models import User, UserRole, UserSession
from ..services import auth
from ..services.audit import log_event
from .deps import COOKIE, SECURE_COOKIE, SessionDep, client_ip, cookie_secure, load_auth, session_token

router = APIRouter(prefix="/api/auth", tags=["autenticação"])

GENERIC_LOGIN_ERROR = "Credenciais inválidas ou conta temporariamente bloqueada."
ALL_STAGES = ("mfa_setup", "mfa", "password", "ok")


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=auth.MAX_PASSWORD)


class SetupBody(BaseModel):
    code: str = Field(max_length=64)
    username: str = Field(max_length=32)
    display_name: str = Field("", max_length=100)
    password: str = Field(max_length=auth.MAX_PASSWORD)


class CodeBody(BaseModel):
    code: str = Field(max_length=32)


class PasswordBody(BaseModel):
    current_password: str = Field(max_length=auth.MAX_PASSWORD)
    new_password: str = Field(max_length=auth.MAX_PASSWORD)


class ConfirmBody(BaseModel):
    password: str = Field(max_length=auth.MAX_PASSWORD)


class DisableMfaBody(BaseModel):
    password: str = Field(max_length=auth.MAX_PASSWORD)
    code: str = Field(max_length=32)


# ----------------------------------------------------------------------------- cookie
def _set_cookie(request: Request, response: Response, token: str) -> None:
    secure = cookie_secure(request)
    _clear_cookie(response)
    # O cookie expira com o navegador; a validade real (inatividade / máximo) é controlada no servidor.
    response.set_cookie(SECURE_COOKIE if secure else COOKIE, token, httponly=True, secure=secure,
                        samesite="strict", path="/")


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
    response.delete_cookie(SECURE_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


def _rotate(request: Request, response: Response, s: Session, old: UserSession | None, user: User,
            stage: str) -> UserSession:
    """Termina a sessão atual e cria uma nova (token novo) na etapa indicada."""
    if old is not None:
        s.delete(old)
        s.commit()
    token = auth.create_session(s, user, stage, client_ip(request), request.headers.get("user-agent", ""))
    _set_cookie(request, response, token)
    return s.get(UserSession, auth.token_hash(token))


def _user_out(user: User) -> dict:
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role,
            "totp_enabled": user.totp_enabled, "must_change_password": user.must_change_password,
            "recovery_codes_left": len(user.recovery_codes or []),
            "last_login_at": user.last_login_at, "password_changed_at": user.password_changed_at}


def _state(sess: UserSession, user: User) -> dict:
    return {"stage": sess.stage, "csrf_token": sess.csrf_token, "user": _user_out(user),
            "idle_minutes": settings.session_idle_minutes, "require_mfa": auth.mfa_required()}


# ----------------------------------------------------------------------------- estado e login
@router.get("/status")
def status(session: SessionDep) -> dict:
    """Público: só diz se falta criar o primeiro administrador (pela linha de comandos)."""
    return {"setup_required": auth.setup_required(session)}


@router.post("/setup", status_code=201)
def setup(body: SetupBody, session: SessionDep) -> dict:
    """Primeiro administrador, no browser, com o código de configuração do servidor (só enquanto não há nenhum)."""
    if not auth.setup_required(session):
        raise HTTPException(409, "A configuração inicial já foi feita. Inicie sessão.")
    if not auth.check_setup_code(body.code):
        log_event(session, "auth.setup_failed", actor="?", details={"username": body.username})
        raise HTTPException(403, "Código de configuração inválido. Veja o ficheiro setup-code.txt na pasta de dados "
                                 "ou os registos do servidor.")
    username = body.username.strip().lower()
    if not auth.USERNAME_RE.fullmatch(username):
        raise HTTPException(422, "Nome de utilizador inválido: 3–32 caracteres (a-z, 0-9, '.', '_', '-').")
    problem = auth.password_problem(body.password, username)
    if problem:
        raise HTTPException(422, problem)
    if session.exec(select(User).where(User.username == username)).first() is not None:
        raise HTTPException(409, "Já existe um utilizador com esse nome.")
    session.add(User(username=username, display_name=body.display_name.strip(), role=UserRole.admin,
                     password_hash=auth.hash_password(body.password), must_change_password=False,
                     password_changed_at=auth.now()))
    session.commit()
    auth.setup_code_path().unlink(missing_ok=True)  # uso único
    log_event(session, "user.created", actor=username,
              details={"user": username, "role": "admin", "via": "configuração inicial no browser"})
    return {"username": username}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, session: SessionDep) -> dict:
    ip = client_ip(request)
    username = body.username.strip().lower()
    if auth.ip_blocked(session, ip):
        log_event(session, "auth.login_blocked_ip", actor=username or "?", details={"username": username})
        raise HTTPException(429, "Demasiadas tentativas falhadas a partir deste endereço. Tente mais tarde.")
    user = session.exec(select(User).where(User.username == username)).first() if username else None
    ok = auth.verify_password(user, body.password)  # corre sempre (tempo constante)
    if not ok or user is None or not user.active or auth.is_locked(user):
        was_locked = auth.is_locked(user)
        auth.register_failure(session, user if (user is not None and not was_locked) else None, username, ip)
        log_event(session, "auth.login_failed", actor=username or "?",
                  details={"username": username, "reason": "bloqueada" if was_locked else "credenciais"})
        if user is not None and not was_locked and auth.is_locked(user):
            log_event(session, "auth.account_locked", actor=username,
                      details={"failed_logins": user.failed_logins, "until": user.locked_until.isoformat()})
        raise HTTPException(401, GENERIC_LOGIN_ERROR)
    auth.rehash_if_needed(session, user, body.password)
    stage = auth.next_stage(user)
    if stage in ("ok", "password"):  # sem MFA: a password conclui o login
        auth.register_success(session, user, ip)
        user.last_login_at = auth.now()
        session.add(user)
    session.commit()
    old = _current(request, session)
    sess = _rotate(request, response, session, old, user, stage)
    if stage in ("ok", "password"):
        log_event(session, "auth.login", actor=user.username, details={"method": "password"})
    return _state(sess, user)


def _current(request: Request, s: Session) -> UserSession | None:
    loaded = auth.load_session(s, session_token(request))
    return loaded[0] if loaded else None


@router.get("/me")
def me(request: Request, session: SessionDep) -> dict:
    """Sessão atual (qualquer etapa) e token CSRF a enviar no cabeçalho X-CSRF-Token."""
    sess, user = load_auth(request, session, stages=ALL_STAGES)
    return _state(sess, user)


# ----------------------------------------------------------------------------- MFA
@router.post("/mfa/setup")
def mfa_setup(request: Request, session: SessionDep) -> dict:
    """Gera um novo segredo TOTP (ainda não ativo) e devolve o QR code para a app autenticadora.

    No login (etapa "mfa_setup", MFA obrigatório) ou depois, em "A minha conta", para quem ainda não o tem."""
    _, user = load_auth(request, session, stages=("mfa_setup", "ok"))
    if user.totp_enabled:
        raise HTTPException(409, "O MFA já está ativo. Desative-o primeiro para o configurar de novo.")
    secret = auth.new_totp_secret()
    user.totp_secret_enc = auth.encrypt(secret)
    user.totp_last_step = 0
    session.add(user)
    session.commit()
    uri, qr = auth.totp_provisioning(user, secret)
    return {"otpauth_uri": uri, "qr_svg": qr, "secret": secret}


@router.post("/mfa/verify")
def mfa_verify(body: CodeBody, request: Request, response: Response, session: SessionDep) -> dict:
    sess, user = load_auth(request, session, stages=("mfa_setup", "mfa"))
    ip = client_ip(request)
    setup = sess.stage == "mfa_setup"
    if auth.is_locked(user):
        raise HTTPException(401, GENERIC_LOGIN_ERROR)
    if setup and not user.totp_secret_enc:
        raise HTTPException(409, "Gere primeiro o QR code.")
    method = "totp"
    ok = auth.verify_totp(user, body.code)
    if not ok and not setup and auth.use_recovery_code(user, body.code):
        ok, method = True, "recovery_code"
    if not ok:
        session.add(user)
        auth.register_failure(session, user, user.username, ip)
        log_event(session, "auth.mfa_failed", actor=user.username, details={"setup": setup})
        if auth.is_locked(user):
            log_event(session, "auth.account_locked", actor=user.username,
                      details={"failed_logins": user.failed_logins, "until": user.locked_until.isoformat()})
            session.delete(sess)
            session.commit()
            _clear_cookie(response)
            raise HTTPException(401, GENERIC_LOGIN_ERROR)
        raise HTTPException(400, "Código inválido.")
    codes: list[str] = []
    if setup:
        user.totp_enabled = True
        codes = auth.new_recovery_codes(user)
    auth.register_success(session, user, ip)
    user.last_login_at = auth.now()
    session.add(user)
    session.commit()
    if setup:
        log_event(session, "auth.mfa_enabled", actor=user.username)
    stage = auth.stage_after_mfa(user)
    new = _rotate(request, response, session, sess, user, stage)
    log_event(session, "auth.login", actor=user.username,
              details={"method": method, "recovery_codes_left": len(user.recovery_codes or [])})
    return {**_state(new, user), "recovery_codes": codes}


@router.post("/mfa/enable")
def mfa_enable(body: CodeBody, request: Request, session: SessionDep) -> dict:
    """Ativa o MFA a partir de uma sessão completa (A minha conta), confirmando o primeiro código do QR."""
    _, user = load_auth(request, session)
    if user.totp_enabled:
        raise HTTPException(409, "O MFA já está ativo.")
    if not user.totp_secret_enc:
        raise HTTPException(409, "Gere primeiro o QR code.")
    if not auth.verify_totp(user, body.code):
        session.add(user)
        auth.register_failure(session, user, user.username, client_ip(request))
        log_event(session, "auth.mfa_failed", actor=user.username, details={"setup": True})
        raise HTTPException(400, "Código inválido.")
    user.totp_enabled = True
    codes = auth.new_recovery_codes(user)
    session.add(user)
    session.commit()
    log_event(session, "auth.mfa_enabled", actor=user.username)
    return {"recovery_codes": codes}


@router.post("/mfa/disable")
def mfa_disable(body: DisableMfaBody, request: Request, session: SessionDep) -> dict:
    """Desativa o MFA do próprio utilizador: pede a password e um código (TOTP ou de recuperação)."""
    sess, user = load_auth(request, session)
    if auth.mfa_required():
        raise HTTPException(403, "O MFA é obrigatório nesta instalação.")
    if not user.totp_enabled:
        raise HTTPException(409, "O MFA não está ativo.")
    ok = auth.verify_password(user, body.password) and (
        auth.verify_totp(user, body.code) or auth.use_recovery_code(user, body.code))
    if not ok:
        session.add(user)
        auth.register_failure(session, user, user.username, client_ip(request))
        raise HTTPException(400, "Password ou código incorretos.")
    user.totp_secret_enc, user.totp_enabled, user.totp_last_step, user.recovery_codes = None, False, 0, []
    session.add(user)
    session.commit()
    ended = auth.end_sessions(session, user.id, except_id=sess.id)
    log_event(session, "auth.mfa_disabled", actor=user.username, details={"other_sessions_ended": ended})
    return _state(sess, user)


@router.post("/mfa/recovery-codes")
def regenerate_recovery_codes(body: ConfirmBody, request: Request, session: SessionDep) -> dict:
    """Gera 10 códigos novos (os anteriores deixam de valer). Pede a password para confirmar."""
    _, user = load_auth(request, session)
    if not user.totp_enabled:
        raise HTTPException(409, "O MFA não está ativo.")
    if not auth.verify_password(user, body.password):
        auth.register_failure(session, user, user.username, client_ip(request))
        raise HTTPException(400, "Password incorreta.")
    codes = auth.new_recovery_codes(user)
    session.add(user)
    session.commit()
    log_event(session, "auth.recovery_codes_regenerated", actor=user.username)
    return {"recovery_codes": codes}


# ----------------------------------------------------------------------------- password
@router.post("/password")
def change_password(body: PasswordBody, request: Request, response: Response, session: SessionDep) -> dict:
    sess, user = load_auth(request, session, stages=("password", "ok"))
    if not auth.verify_password(user, body.current_password):
        auth.register_failure(session, user, user.username, client_ip(request))
        log_event(session, "auth.password_change_failed", actor=user.username)
        raise HTTPException(400, "A password atual está incorreta.")
    problem = auth.password_problem(body.new_password, user.username)
    if problem:
        raise HTTPException(422, problem)
    if body.new_password == body.current_password:
        raise HTTPException(422, "A nova password tem de ser diferente da atual.")
    user.password_hash = auth.hash_password(body.new_password)
    user.must_change_password = False
    user.password_changed_at = auth.now()
    session.add(user)
    session.commit()
    ended = auth.end_sessions(session, user.id, except_id=sess.id)
    new = _rotate(request, response, session, sess, user, "ok")
    log_event(session, "auth.password_changed", actor=user.username, details={"other_sessions_ended": ended})
    return _state(new, user)


# ----------------------------------------------------------------------------- sessões
@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, session: SessionDep) -> Response:
    sess, user = load_auth(request, session, stages=ALL_STAGES)
    session.delete(sess)
    session.commit()
    log_event(session, "auth.logout", actor=user.username)
    response.status_code = 204
    _clear_cookie(response)
    return response


@router.get("/sessions")
def list_sessions(request: Request, session: SessionDep) -> list[dict]:
    sess, user = load_auth(request, session)
    rows = session.exec(select(UserSession).where(UserSession.user_id == user.id, UserSession.stage == "ok")
                        .order_by(UserSession.last_seen_at.desc()))
    return [{"id": r.id[:16], "current": r.id == sess.id, "created_at": r.created_at,
             "last_seen_at": r.last_seen_at, "expires_at": r.expires_at, "ip": r.ip, "user_agent": r.user_agent}
            for r in rows]


@router.delete("/sessions", status_code=204)
def end_other_sessions(request: Request, session: SessionDep) -> None:
    """Termina todas as outras sessões do utilizador (mantém a atual)."""
    sess, user = load_auth(request, session)
    n = auth.end_sessions(session, user.id, except_id=sess.id)
    log_event(session, "auth.sessions_ended", actor=user.username, details={"count": n})


@router.delete("/sessions/{sid}", status_code=204)
def end_session(sid: str, request: Request, session: SessionDep) -> None:
    sess, user = load_auth(request, session)
    if len(sid) < 16:
        raise HTTPException(404, "Sessão não encontrada.")
    rows = [r for r in session.exec(select(UserSession).where(UserSession.user_id == user.id))
            if r.id.startswith(sid) and r.id != sess.id]
    if len(rows) != 1:
        raise HTTPException(404, "Sessão não encontrada.")
    session.delete(rows[0])
    session.commit()
    log_event(session, "auth.session_ended", actor=user.username, details={"session": sid})
