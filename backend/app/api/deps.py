"""Dependências comuns: sessão da base de dados, utilizador autenticado, CSRF e autorização por caso.

Autorização (anti-IDOR): os routers de dados usam `authorize_objects`, que resolve o caso a que pertence qualquer
objeto indicado no caminho (case_id, job_id, video_id, ref_id, det_id, track_id, upload_id,
case_person_id) e responde 404 se o utilizador não tiver acesso a esse caso — também para objetos que não
existem, para não revelar a sua existência.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..models import (
    Alert,
    Case,
    CaseMember,
    CasePerson,
    Detection,
    Job,
    Location,
    ReferenceImage,
    SavedFrame,
    Track,
    Upload,
    User,
    UserRole,
    UserSession,
    Video,
)
from ..services import auth

SessionDep = Annotated[Session, Depends(get_session)]
COOKIE = "faceai_session"
SECURE_COOKIE = "__Host-faceai_session"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def client_ip(request: Request) -> str:
    if settings.trust_proxy == "cloudflare":
        ip = request.headers.get("cf-connecting-ip")
        if ip:
            return ip.strip()[:64]
    return request.client.host if request.client else ""


def is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return settings.trust_proxy == "cloudflare" and request.headers.get("x-forwarded-proto", "") == "https"


def cookie_secure(request: Request) -> bool:
    if settings.cookie_secure in ("true", "1", "yes"):
        return True
    if settings.cookie_secure in ("false", "0", "no"):
        return False
    return is_https(request)


def session_token(request: Request) -> str | None:
    return request.cookies.get(SECURE_COOKIE) or request.cookies.get(COOKIE)


def _check_csrf(request: Request, sess: UserSession) -> None:
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not hmac.compare_digest(sent, sess.csrf_token):
            raise HTTPException(403, "Pedido recusado (token CSRF em falta ou inválido). Recarregue a página.")


def load_auth(request: Request, session: SessionDep, *, stages: tuple[str, ...] = ("ok",)
              ) -> tuple[UserSession, User]:
    loaded = auth.load_session(session, session_token(request))
    if loaded is None:
        raise HTTPException(401, "Sessão inexistente ou expirada. Inicie sessão.")
    sess, user = loaded
    if sess.stage not in stages:
        raise HTTPException(401, "Autenticação incompleta.")
    _check_csrf(request, sess)
    request.state.user = user
    request.state.session = sess
    return sess, user


def get_current_user(request: Request, session: SessionDep) -> User:
    return load_auth(request, session)[1]


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_actor(user: CurrentUser) -> str:
    """Quem fica registado na auditoria: o utilizador autenticado (nunca um valor enviado pelo cliente)."""
    return user.username


ActorDep = Annotated[str, Depends(get_actor)]


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(403, "Só um administrador pode fazer isto.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


# ----------------------------------------------------------------------------- autorização por caso
def membership(session: Session, user: User, case_id: int) -> CaseMember | None:
    return session.exec(select(CaseMember).where(CaseMember.case_id == case_id,
                                                 CaseMember.user_id == user.id)).first()


def has_case_access(session: Session, user: User, case_id: int) -> bool:
    return user.role == UserRole.admin or membership(session, user, case_id) is not None


def can_manage_case(session: Session, user: User, case_id: int) -> bool:
    if user.role == UserRole.admin:
        return True
    m = membership(session, user, case_id)
    return m is not None and m.role == "owner"


def accessible_case_ids(session: Session, user: User) -> set[int] | None:
    """None = todos (admin)."""
    if user.role == UserRole.admin:
        return None
    return {m.case_id for m in session.exec(select(CaseMember).where(CaseMember.user_id == user.id))}


def _case_of(session: Session, params: dict) -> int | None:
    if "case_id" in params:
        return int(params["case_id"])
    if "job_id" in params:
        job = session.get(Job, int(params["job_id"]))
        return job.case_id if job else -1
    if "video_id" in params:
        v = session.get(Video, int(params["video_id"]))
        return v.case_id if v else -1
    if "ref_id" in params:
        r = session.get(ReferenceImage, int(params["ref_id"]))
        return r.case_id if r else -1
    if "det_id" in params:  # uma só consulta (pedido frequente: cada imagem de uma grelha)
        cid = session.exec(select(Job.case_id).join(Detection, Detection.job_id == Job.id)
                           .where(Detection.id == int(params["det_id"]))).first()
        return cid if cid is not None else -1
    if "track_id" in params:
        cid = session.exec(select(Job.case_id).join(Track, Track.job_id == Job.id)
                           .where(Track.id == int(params["track_id"]))).first()
        return cid if cid is not None else -1
    if "case_person_id" in params:
        cp = session.get(CasePerson, int(params["case_person_id"]))
        return cp.case_id if cp else -1
    if "location_id" in params:
        loc = session.get(Location, int(params["location_id"]))
        return loc.case_id if loc else -1
    if "alert_id" in params:
        al = session.get(Alert, int(params["alert_id"]))
        return al.case_id if al else -1
    if "saved_frame_id" in params:
        sf = session.get(SavedFrame, int(params["saved_frame_id"]))
        return sf.case_id if sf else -1
    if "upload_id" in params:
        u = session.get(Upload, str(params["upload_id"]))
        return u.case_id if u else -1
    return None


def authorize_objects(request: Request, session: SessionDep, user: CurrentUser) -> None:
    """Dependência dos routers de dados: o objeto do caminho tem de pertencer a um caso acessível."""
    try:
        case_id = _case_of(session, request.path_params)
    except (TypeError, ValueError) as exc:
        raise HTTPException(404, "Não encontrado.") from exc
    if case_id is None:
        return
    if case_id < 0 or not has_case_access(session, user, case_id):
        raise HTTPException(404, "Não encontrado.")


def get_case_or_404(session: Session, case_id: int) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "Processo não encontrado.")
    return case


def get_job_or_404(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Análise não encontrada.")
    return job
