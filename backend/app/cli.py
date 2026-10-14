"""Administração local (consola do servidor): python -m app.cli <comando>.

  create-admin [username]   cria um administrador (pede a password; não há registo público)
  reset-password <username> define uma password temporária e termina as sessões
  reset-mfa <username>      remove o MFA (volta a ser configurado no próximo login)
  unlock <username>         desbloqueia a conta
  list-users                lista os utilizadores

Com Docker: docker compose exec faceai python -m app.cli create-admin
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlmodel import Session, select

from .db import get_engine, init_db
from .models import User, UserRole
from .services import auth
from .services.audit import log_event

ACTOR = "cli"


def _ask_password(username: str) -> str:
    while True:
        pw = getpass.getpass("Password: ")
        problem = auth.password_problem(pw, username)
        if problem:
            print(problem, file=sys.stderr)
            continue
        if getpass.getpass("Repetir password: ") != pw:
            print("As passwords não coincidem.", file=sys.stderr)
            continue
        return pw


def _find(s: Session, username: str) -> User:
    u = s.exec(select(User).where(User.username == username.strip().lower())).first()
    if u is None:
        sys.exit(f"Utilizador '{username}' não existe.")
    return u


def create_admin(s: Session, username: str | None, display_name: str) -> None:
    username = (username or input("Nome de utilizador: ")).strip().lower()
    if not auth.USERNAME_RE.fullmatch(username):
        sys.exit("Nome inválido: 3–32 caracteres (a-z, 0-9, '.', '_', '-').")
    if s.exec(select(User).where(User.username == username)).first() is not None:
        sys.exit(f"Já existe o utilizador '{username}'.")
    pw = _ask_password(username)
    # Quem tem acesso à consola escolheu a password: não é temporária. O MFA é configurado no primeiro login.
    s.add(User(username=username, display_name=display_name, role=UserRole.admin,
               password_hash=auth.hash_password(pw), must_change_password=False,
               password_changed_at=auth.now()))
    s.commit()
    log_event(s, "user.created", actor=ACTOR, ip="local", details={"user": username, "role": "admin"})
    print(f"Administrador '{username}' criado. No primeiro login vai configurar o MFA (app autenticadora).")


def reset_password(s: Session, username: str) -> None:
    u = _find(s, username)
    u.password_hash = auth.hash_password(_ask_password(u.username))
    u.must_change_password = True
    u.failed_logins, u.locked_until = 0, None
    s.add(u)
    s.commit()
    auth.end_sessions(s, u.id)
    log_event(s, "user.password_reset", actor=ACTOR, ip="local", details={"user": u.username})
    print("Password temporária definida (tem de ser mudada no próximo login).")


def reset_mfa(s: Session, username: str) -> None:
    u = _find(s, username)
    u.totp_secret_enc, u.totp_enabled, u.totp_last_step, u.recovery_codes = None, False, 0, []
    s.add(u)
    s.commit()
    auth.end_sessions(s, u.id)
    log_event(s, "user.mfa_reset", actor=ACTOR, ip="local", details={"user": u.username})
    print("MFA removido: será configurado de novo no próximo login.")


def unlock(s: Session, username: str) -> None:
    u = _find(s, username)
    u.failed_logins, u.locked_until, u.active = 0, None, True
    s.add(u)
    s.commit()
    log_event(s, "user.unlocked", actor=ACTOR, ip="local", details={"user": u.username})
    print("Conta desbloqueada e ativa.")


def list_users(s: Session) -> None:
    for u in s.exec(select(User).order_by(User.username)):
        flags = [u.role, "ativo" if u.active else "inativo", "MFA" if u.totp_enabled else "sem MFA"]
        if auth.is_locked(u):
            flags.append("bloqueado")
        print(f"{u.username:24} {', '.join(flags)}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="Administração local do FaceAI.")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin")
    c.add_argument("username", nargs="?")
    c.add_argument("--name", default="", help="nome a apresentar")
    for name in ("reset-password", "reset-mfa", "unlock"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("list-users")
    args = p.parse_args(argv)

    init_db()
    with Session(get_engine()) as s:
        if args.cmd == "create-admin":
            create_admin(s, args.username, args.name)
        elif args.cmd == "reset-password":
            reset_password(s, args.username)
        elif args.cmd == "reset-mfa":
            reset_mfa(s, args.username)
        elif args.cmd == "unlock":
            unlock(s, args.username)
        else:
            list_users(s)


if __name__ == "__main__":
    main()
