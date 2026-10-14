"""Autenticação: passwords (Argon2id), MFA TOTP obrigatório, sessões por cookie e proteção contra força bruta.

- Passwords: Argon2id (argon2-cffi, parâmetros RFC 9106), mínimo 12 caracteres, recusa passwords comuns e as que
  contêm o nome de utilizador. Sem regras de composição forçadas (NIST SP 800-63B).
- TOTP (RFC 6238, 30 s, 6 dígitos), janela ±1 passo e anti-replay (último passo aceite). O segredo fica cifrado
  com AES-GCM e uma chave do servidor (FACEAI_SECRET_KEY ou data/secret.key, permissões 0600).
- Códigos de recuperação: 10, de uso único, guardados como SHA-256 (têm 50 bits de entropia cada).
- Sessões: token aleatório de 256 bits no cookie; na base de dados só fica o SHA-256. Expiram por inatividade e
  ao fim de um tempo máximo; o token muda em cada passo do login (fixação de sessão).
- Bloqueio progressivo por conta (5/10/15 falhas → 1/5/15 min) e limite por IP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlmodel import Session, select

from ..config import settings
from ..models import LoginAttempt, User, UserRole, UserSession, utcnow

MIN_PASSWORD = 12
MAX_PASSWORD = 256
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
TOTP_ISSUER = "FaceAI"
RECOVERY_CODES = 10
PENDING_SESSION_MINUTES = 10  # sessões a meio do login (MFA / mudança de password)
IP_WINDOW = timedelta(minutes=15)
IP_MAX_FAILURES = 30
LOCK_STEPS = ((15, 15), (10, 5), (5, 1))  # (falhas seguidas, minutos de bloqueio)

hasher = PasswordHasher()  # Argon2id, t=3, m=64 MiB, p=4 (RFC 9106, perfil de baixa memória)
_DUMMY_HASH = hasher.hash("faceai-dummy-password-for-timing")


def now() -> datetime:
    return utcnow()


def _aware(dt: datetime | None) -> datetime | None:
    """O SQLite devolve datas sem fuso; todas as datas desta app são UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ----------------------------------------------------------------------------- passwords
@lru_cache(maxsize=1)
def _common_passwords() -> frozenset[str]:
    path = Path(__file__).resolve().parents[1] / "data" / "common_passwords.txt"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def password_problem(password: str, username: str = "") -> str | None:
    """Motivo pelo qual a password não é aceite, ou None."""
    if len(password) < MIN_PASSWORD:
        return f"A password tem de ter pelo menos {MIN_PASSWORD} caracteres."
    if len(password) > MAX_PASSWORD:
        return f"A password não pode ter mais de {MAX_PASSWORD} caracteres."
    low = password.lower()
    if low in _common_passwords():
        return "Esta password é demasiado comum (consta de listas de passwords divulgadas)."
    if username and username.lower() in low:
        return "A password não pode conter o nome de utilizador."
    if len(set(low)) <= 3:
        return "A password é demasiado repetitiva."
    if "faceai" in low:
        return "A password não pode conter o nome da aplicação."
    return None


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(user: User | None, password: str) -> bool:
    """Tempo constante quer o utilizador exista quer não (evita enumerar contas pelo tempo de resposta)."""
    target = user.password_hash if user is not None else _DUMMY_HASH
    try:
        ok = hasher.verify(target, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        ok = False
    return ok and user is not None


def rehash_if_needed(s: Session, user: User, password: str) -> None:
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hasher.hash(password)
        s.add(user)


# ----------------------------------------------------------------------------- chave do servidor / cifra
@lru_cache(maxsize=1)
def _server_key() -> bytes:
    if settings.secret_key:
        return hashlib.sha256(settings.secret_key.encode()).digest()
    path = settings.data_dir / "secret.key"
    if not path.exists():
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(base64.b64encode(secrets.token_bytes(32)))
    return hashlib.sha256(path.read_bytes().strip()).digest()


def encrypt(text: str) -> str:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_server_key()).encrypt(nonce, text.encode(), b"faceai-totp")
    return base64.b64encode(nonce + ct).decode()


def decrypt(blob: str) -> str:
    raw = base64.b64decode(blob)
    return AESGCM(_server_key()).decrypt(raw[:12], raw[12:], b"faceai-totp").decode()


# ----------------------------------------------------------------------------- TOTP e códigos de recuperação
def new_totp_secret() -> str:
    return pyotp.random_base32(32)  # 160 bits


def totp_provisioning(user: User, secret: str) -> tuple[str, str]:
    """(URI otpauth, QR code em SVG como data URI) — gerado localmente, sem serviços externos."""
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=TOTP_ISSUER)
    qr = segno.make(uri, error="m")
    return uri, qr.svg_data_uri(scale=6, border=2)


def verify_totp(user: User, code: str, secret: str | None = None, at: float | None = None) -> bool:
    """Aceita o passo atual ±1, nunca um passo já usado (anti-replay). Atualiza user.totp_last_step."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", code):
        return False
    secret = secret or (decrypt(user.totp_secret_enc) if user.totp_secret_enc else None)
    if not secret:
        return False
    totp = pyotp.TOTP(secret)
    t = time.time() if at is None else at
    step = int(t // 30)
    for offset in (0, -1, 1):
        s = step + offset
        if s <= (user.totp_last_step or 0):
            continue
        if hmac.compare_digest(totp.at(s * 30), code):
            user.totp_last_step = s
            return True
    return False


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


def new_recovery_codes(user: User) -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = base64.b32encode(secrets.token_bytes(5)).decode().lower()  # 8 caracteres, 40 bits
        codes.append(f"{raw[:4]}-{raw[4:]}")
    user.recovery_codes = [_hash_code(c) for c in codes]
    return codes


def use_recovery_code(user: User, code: str) -> bool:
    h = _hash_code(code or "")
    codes = list(user.recovery_codes or [])
    for stored in codes:
        if hmac.compare_digest(stored, h):
            codes.remove(stored)
            user.recovery_codes = codes
            return True
    return False


# ----------------------------------------------------------------------------- bloqueios
def is_locked(user: User | None) -> bool:
    lu = _aware(user.locked_until) if user is not None else None
    return lu is not None and lu > now()


def register_failure(s: Session, user: User | None, username: str, ip: str) -> None:
    s.add(LoginAttempt(username=username[:64], ip=ip, success=False))
    if user is not None:
        user.failed_logins += 1
        for count, minutes in LOCK_STEPS:
            if user.failed_logins >= count and user.failed_logins % 5 == 0:
                user.locked_until = now() + timedelta(minutes=minutes)
                break
        s.add(user)
    s.commit()


def register_success(s: Session, user: User, ip: str) -> None:
    s.add(LoginAttempt(username=user.username, ip=ip, success=True))
    user.failed_logins = 0
    user.locked_until = None
    s.add(user)


def ip_blocked(s: Session, ip: str) -> bool:
    if not ip:
        return False
    since = now() - IP_WINDOW
    failures = s.exec(select(LoginAttempt).where(LoginAttempt.ip == ip, LoginAttempt.success == False,  # noqa: E712
                                                 LoginAttempt.at >= since)).all()
    return len(failures) >= IP_MAX_FAILURES


# ----------------------------------------------------------------------------- sessões
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(s: Session, user: User, stage: str, ip: str, user_agent: str) -> str:
    """Cria a sessão e devolve o token (só vai no cookie; a base de dados guarda o hash)."""
    token = secrets.token_urlsafe(32)
    ttl = (timedelta(minutes=PENDING_SESSION_MINUTES) if stage != "ok"
           else timedelta(hours=settings.session_max_hours))
    s.add(UserSession(id=token_hash(token), user_id=user.id, stage=stage, csrf_token=secrets.token_urlsafe(32),
                      expires_at=now() + ttl, ip=ip, user_agent=user_agent[:300]))
    s.commit()
    return token


def load_session(s: Session, token: str | None) -> tuple[UserSession, User] | None:
    if not token:
        return None
    sess = s.get(UserSession, token_hash(token))
    if sess is None:
        return None
    t = now()
    idle = timedelta(minutes=settings.session_idle_minutes)
    if _aware(sess.expires_at) <= t or (sess.stage == "ok" and _aware(sess.last_seen_at) + idle <= t):
        s.delete(sess)
        s.commit()
        return None
    user = s.get(User, sess.user_id)
    if user is None or not user.active:
        return None
    if (t - _aware(sess.last_seen_at)).total_seconds() > 60:  # não escrever na BD em todos os pedidos
        sess.last_seen_at = t
        s.add(sess)
        s.commit()
    return sess, user


def end_sessions(s: Session, user_id: int, except_id: str | None = None) -> int:
    rows = [x for x in s.exec(select(UserSession).where(UserSession.user_id == user_id)) if x.id != except_id]
    for x in rows:
        s.delete(x)
    s.commit()
    return len(rows)


def next_stage(user: User) -> str:
    if not user.totp_enabled:
        return "mfa_setup"
    return "mfa"


def stage_after_mfa(user: User) -> str:
    return "password" if user.must_change_password else "ok"


def admins_active(s: Session) -> int:
    return len(s.exec(select(User).where(User.role == UserRole.admin, User.active == True)).all())  # noqa: E712


def setup_required(s: Session) -> bool:
    return admins_active(s) == 0
