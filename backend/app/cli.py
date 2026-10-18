"""Administração local (consola do servidor): python -m app.cli <comando>.

  create-admin [username]   cria um administrador (pede a password; não há registo público)
  reset-password <username> define uma password temporária e termina as sessões
  reset-mfa <username>      remove o MFA (ex.: telemóvel perdido)
  unlock <username>         desbloqueia a conta
  list-users                lista os utilizadores
  verify-audit              verifica a cadeia de hashes da auditoria (alterações, eventos apagados)
  verify-evidence [case_id] recalcula o SHA-256 dos vídeos originais e compara com o da importação
  backup <pasta> [--files]  cópia consistente da base de dados (VACUUM INTO) + manifesto; com --files,
                            copia também os ficheiros dos processos (incremental)
  remove-originals [case_id] [--dry-run] [--yes]
                            apaga do servidor os originais já convertidos (cópia de análise sem perdas verificada);
                            fica o SHA-256 para os relacionar com o original guardado no suporte de origem
  sec-info <ficheiro.sec> [--extract <destino>]
                            mostra a estrutura de um ficheiro .sec de DVR (blocos, streams, cabeçalhos) e, com
                            --extract, grava o vídeo extraído (destino.mp4, ou destino.h264 sem ffmpeg)

Com Docker: docker compose exec faceai python -m app.cli create-admin
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from sqlmodel import Session, select

from .db import get_engine, init_db
from .models import User, UserRole, Video
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
    # Quem tem acesso à consola escolheu a password: não é temporária. O MFA ativa-se em "A minha conta"
    # (ou é configurado logo no primeiro login, se FACEAI_REQUIRE_MFA=true).
    s.add(User(username=username, display_name=display_name, role=UserRole.admin,
               password_hash=auth.hash_password(pw), must_change_password=False,
               password_changed_at=auth.now()))
    s.commit()
    log_event(s, "user.created", actor=ACTOR, ip="local", details={"user": username, "role": "admin"})
    print(f"Administrador '{username}' criado. Recomendado: ativar o MFA em 'A minha conta' depois do login.")


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
    print("MFA removido. O utilizador pode voltar a ativá-lo em 'A minha conta'.")


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


def verify_audit(s: Session) -> None:
    from .services.audit import verify_chain

    r = verify_chain(s)
    log_event(s, "audit.verified", actor=ACTOR, ip="local",
              details={k: r[k] for k in ("ok", "events", "unsealed", "broken_at", "last_hash")})
    if r["ok"]:
        print(f"Cadeia íntegra: {r['events']} evento(s); último hash {r['last_hash']}.")
    else:
        sys.exit(f"CADEIA QUEBRADA no evento #{r['broken_at']}: {r['reason']}.")
    if r["unsealed"]:
        print(f"Atenção: {r['unsealed']} evento(s) sem hash.")


def verify_evidence(s: Session, case_id: int | None) -> None:
    from .services.evidence import verify_videos

    q = select(Video).order_by(Video.case_id, Video.id)
    if case_id is not None:
        q = q.where(Video.case_id == case_id)
    by_case: dict[int, list[Video]] = {}
    for v in s.exec(q):
        by_case.setdefault(v.case_id, []).append(v)
    bad = 0
    for cid, videos in by_case.items():
        summary = verify_videos(s, videos, case_id=cid, actor=ACTOR)
        for f in summary["failed"]:
            bad += 1
            print(f"Processo {cid} · vídeo #{f['video_id']} {f['filename']}: {f['reason']}")
        print(f"Processo {cid}: {summary['ok']}/{summary['videos']} vídeo(s) íntegro(s).")
    if bad:
        sys.exit(1)


def run_backup(dest: str, include_files: bool) -> None:
    from .services.backup import backup

    m = backup(Path(dest), include_files=include_files)
    print(f"Base de dados: {Path(dest) / m['database']['file']} (SHA-256 {m['database']['sha256']}).")
    if include_files:
        print(f"Ficheiros: {m['files']['copied']} copiado(s), {m['files']['unchanged']} sem alterações.")
    print(f"Manifesto: manifest_{m['created_at']}.json ({len(m['videos'])} vídeo(s)).")


def remove_originals(s: Session, case_id: int | None, dry_run: bool, yes: bool) -> None:
    from .models import utcnow
    from .services import originals, sec_extract, transcode

    q = select(Video).where(Video.original_removed_at.is_(None)).order_by(Video.id)
    if case_id is not None:
        q = q.where(Video.case_id == case_id)
    todo = []
    for v in s.exec(q):
        if not Path(v.stored_path).exists() or not sec_extract.is_ready(v):
            continue
        if v.analysis_path:
            todo.append((v, "cópia de análise já existe"))
        elif transcode.ffmpeg_available() and transcode.needs_playback_copy(v)[0]:
            todo.append((v, "cria primeiro a cópia por remux sem perdas"))
    total = sum(v.size_bytes for v, _ in todo)
    for v, how in todo:
        print(f"#{v.id} processo {v.case_id} {v.filename} ({v.size_bytes / 1e9:.2f} GB): {how}")
    print(f"{len(todo)} original(is), {total / 1e9:.2f} GB.")
    if dry_run or not todo:
        return
    if not yes and input("Apagar estes originais do servidor? Escreva 'sim': ").strip().lower() != "sim":
        sys.exit("Nada foi apagado.")
    removed = 0
    for v, _ in todo:
        if not v.analysis_path:
            src = Path(v.stored_path)
            try:
                out, info = originals.remux_for_analysis(src, src.with_name(src.stem + "_analysis"))
            except (OSError, ValueError) as exc:
                print(f"#{v.id}: original mantido ({exc})", file=sys.stderr)
                continue
            v.analysis_path, v.analysis_sha256, v.extraction = str(out), info["analysis_sha256"], info
            v.extraction_status = sec_extract.READY
            s.add(v)
            s.commit()
            log_event(s, "video.analysis_copy_ready", case_id=v.case_id, actor=ACTOR, ip="local",
                      details={"video_id": v.id, "filename": v.filename, "original_sha256": v.sha256,
                               "at": utcnow().isoformat(), **info})
        if originals.remove_original(s, v, actor=ACTOR):
            removed += 1
        else:
            print(f"#{v.id}: original mantido (ver auditoria video.original_kept)", file=sys.stderr)
    print(f"{removed} original(is) apagado(s).")


def sec_info(file: str, extract: str | None) -> None:
    import json

    from .services import sec_format

    if not sec_format.is_sec(file):
        print("O ficheiro não começa por um bloco .sec reconhecido (00 e0 …). Primeiros bytes:\n"
              + sec_format.first_bytes(file, 128), file=sys.stderr)
    if extract:
        out, info = sec_format.extract(file, Path(extract))
        print(json.dumps(info, indent=2, ensure_ascii=False))
        print(f"Vídeo extraído: {out}")
    else:
        print(json.dumps(sec_format.inspect(file), indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="Administração local do FaceAI.")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin")
    c.add_argument("username", nargs="?")
    c.add_argument("--name", default="", help="nome a apresentar")
    for name in ("reset-password", "reset-mfa", "unlock"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("list-users")
    sub.add_parser("verify-audit")
    sub.add_parser("verify-evidence").add_argument("case_id", nargs="?", type=int)
    b = sub.add_parser("backup")
    b.add_argument("dest")
    b.add_argument("--files", action="store_true", help="copiar também os ficheiros dos processos")
    r = sub.add_parser("remove-originals")
    r.add_argument("case_id", nargs="?", type=int)
    r.add_argument("--dry-run", action="store_true", help="só lista o que seria apagado")
    r.add_argument("--yes", action="store_true", help="não pede confirmação")
    x = sub.add_parser("sec-info")
    x.add_argument("file")
    x.add_argument("--extract", metavar="DESTINO", help="grava o vídeo extraído (sem extensão)")
    args = p.parse_args(argv)

    if args.cmd == "sec-info":  # não usa a base de dados
        sec_info(args.file, args.extract)
        return
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
        elif args.cmd == "verify-audit":
            verify_audit(s)
        elif args.cmd == "verify-evidence":
            verify_evidence(s, args.case_id)
        elif args.cmd == "remove-originals":
            remove_originals(s, args.case_id, args.dry_run, args.yes)
        elif args.cmd == "backup":
            run_backup(args.dest, args.files)
        else:
            list_users(s)


if __name__ == "__main__":
    main()
