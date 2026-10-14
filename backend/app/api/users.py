"""Gestão de utilizadores (só administradores) e auditoria do sistema (eventos sem caso: logins, contas…).

Os utilizadores nunca são apagados, só desativados, para a auditoria continuar coerente. Repor a password ou o MFA
termina as sessões do utilizador. O último administrador ativo não pode ser desativado nem despromovido.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from ..models import AuditEvent, CaseMember, User, UserRole
from ..services import auth
from ..services.audit import log_event
from .deps import AdminUser, SessionDep, require_admin

router = APIRouter(prefix="/api", tags=["utilizadores"], dependencies=[Depends(require_admin)])


class UserCreate(BaseModel):
    username: str = Field(max_length=32)
    display_name: str = Field("", max_length=100)
    role: UserRole = UserRole.analyst
    password: str = Field(max_length=auth.MAX_PASSWORD)


class UserUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=100)
    role: UserRole | None = None
    active: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(max_length=auth.MAX_PASSWORD)


def user_out(s, u: User) -> dict:
    cases = s.exec(select(CaseMember).where(CaseMember.user_id == u.id)).all()
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role,
            "active": u.active, "totp_enabled": u.totp_enabled, "must_change_password": u.must_change_password,
            "locked": auth.is_locked(u), "created_at": u.created_at, "last_login_at": u.last_login_at,
            "cases": len(cases)}


def _get(s, user_id: int) -> User:
    u = s.get(User, user_id)
    if u is None:
        raise HTTPException(404, "Utilizador não encontrado.")
    return u


def _check_password(password: str, username: str) -> None:
    problem = auth.password_problem(password, username)
    if problem:
        raise HTTPException(422, problem)


@router.get("/users")
def list_users(session: SessionDep) -> list[dict]:
    return [user_out(session, u) for u in session.exec(select(User).order_by(User.username))]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, session: SessionDep, admin: AdminUser) -> dict:
    username = body.username.strip().lower()
    if not auth.USERNAME_RE.fullmatch(username):
        raise HTTPException(422, "Nome de utilizador inválido: 3–32 caracteres, letras minúsculas, dígitos, '.', '_' "
                                 "ou '-', a começar por letra ou dígito.")
    if session.exec(select(User).where(User.username == username)).first() is not None:
        raise HTTPException(409, "Já existe um utilizador com esse nome.")
    _check_password(body.password, username)
    u = User(username=username, display_name=body.display_name.strip(), role=body.role,
             password_hash=auth.hash_password(body.password), must_change_password=True)
    session.add(u)
    session.commit()
    session.refresh(u)
    log_event(session, "user.created", actor=admin.username,
              details={"user": username, "role": u.role, "display_name": u.display_name})
    return user_out(session, u)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, session: SessionDep, admin: AdminUser) -> dict:
    u = _get(session, user_id)
    changes: dict = {}
    losing_admin = u.role == UserRole.admin and u.active and (
        (body.role is not None and body.role != UserRole.admin) or body.active is False)
    if losing_admin and auth.admins_active(session) <= 1:
        raise HTTPException(409, "Tem de existir pelo menos um administrador ativo.")
    if body.display_name is not None and body.display_name.strip() != u.display_name:
        changes["display_name"] = [u.display_name, body.display_name.strip()]
        u.display_name = body.display_name.strip()
    if body.role is not None and body.role != u.role:
        changes["role"] = [u.role, body.role]
        u.role = body.role
    if body.active is not None and body.active != u.active:
        changes["active"] = [u.active, body.active]
        u.active = body.active
        if body.active:
            u.failed_logins, u.locked_until = 0, None
    if not changes:
        return user_out(session, u)
    session.add(u)
    session.commit()
    if "active" in changes or "role" in changes:
        auth.end_sessions(session, u.id)  # as permissões mudaram: novo login
    log_event(session, "user.updated", actor=admin.username, details={"user": u.username, "changes": changes})
    return user_out(session, u)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: PasswordReset, session: SessionDep, admin: AdminUser) -> dict:
    """Define uma password temporária (o utilizador tem de a mudar no próximo login)."""
    u = _get(session, user_id)
    _check_password(body.password, u.username)
    u.password_hash = auth.hash_password(body.password)
    u.must_change_password = True
    u.failed_logins, u.locked_until = 0, None
    session.add(u)
    session.commit()
    n = auth.end_sessions(session, u.id)
    log_event(session, "user.password_reset", actor=admin.username, details={"user": u.username, "sessions_ended": n})
    return user_out(session, u)


@router.post("/users/{user_id}/reset-mfa")
def reset_mfa(user_id: int, session: SessionDep, admin: AdminUser) -> dict:
    """Remove o MFA (ex.: telemóvel perdido): no próximo login o utilizador configura-o de novo."""
    u = _get(session, user_id)
    u.totp_secret_enc, u.totp_enabled, u.totp_last_step, u.recovery_codes = None, False, 0, []
    session.add(u)
    session.commit()
    n = auth.end_sessions(session, u.id)
    log_event(session, "user.mfa_reset", actor=admin.username, details={"user": u.username, "sessions_ended": n})
    return user_out(session, u)


@router.post("/users/{user_id}/unlock")
def unlock(user_id: int, session: SessionDep, admin: AdminUser) -> dict:
    u = _get(session, user_id)
    u.failed_logins, u.locked_until = 0, None
    session.add(u)
    session.commit()
    log_event(session, "user.unlocked", actor=admin.username, details={"user": u.username})
    return user_out(session, u)


@router.get("/audit/system", response_model=list[AuditEvent])
def system_audit(session: SessionDep, limit: int = 500) -> list[AuditEvent]:
    """Eventos sem caso associado (autenticação, contas), do mais recente para o mais antigo."""
    q = select(AuditEvent).where(AuditEvent.case_id == None).order_by(AuditEvent.id.desc())  # noqa: E711
    return list(session.exec(q.limit(max(1, min(limit, 5000)))))
