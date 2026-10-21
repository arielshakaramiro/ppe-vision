"""Kelola akun dashboard dari terminal (terutama untuk membuat admin pertama).

    python -m scripts.users add shaka --role admin        # password ditanyakan, tidak tampil di layar
    python -m scripts.users list
    python -m scripts.users passwd shaka
    python -m scripts.users role budi supervisor
    python -m scripts.users disable budi
    python -m scripts.users enable budi
"""
from __future__ import annotations

import argparse
import getpass

from sqlmodel import Session, select

from app import auth
from app.db import User, engine, init_db


def _ask_password() -> str:
    pw = getpass.getpass("Password baru: ")
    if pw != getpass.getpass("Ulangi password: "):
        raise SystemExit("Password tidak sama")
    return pw


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("username")
    a.add_argument("--role", choices=auth.ROLES, default="viewer")
    sub.add_parser("list")
    for name in ("passwd", "disable", "enable"):
        sub.add_parser(name).add_argument("username")
    r = sub.add_parser("role")
    r.add_argument("username")
    r.add_argument("role", choices=auth.ROLES)
    args = ap.parse_args()

    init_db()
    with Session(engine) as db:
        if args.cmd == "list":
            for u in db.exec(select(User).order_by(User.username)).all():
                print(f"{u.username:20} {u.role:11} {'aktif' if u.active else 'nonaktif':9} login terakhir: {u.last_login or '-'}")
            return
        if args.cmd == "add":
            try:
                auth.create_user(db, args.username, _ask_password(), args.role, actor="cli")
            except ValueError as e:
                raise SystemExit(str(e))
            print(f"Akun {args.username} ({args.role}) dibuat.")
            return
        u = db.exec(select(User).where(User.username == args.username)).first()
        if not u:
            raise SystemExit("Pengguna tidak ditemukan")
        if args.cmd == "passwd":
            pw = _ask_password()
            try:
                auth.validate_new_account(u.username, pw, None)
            except ValueError as e:
                raise SystemExit(str(e))
            u.password_hash = auth.hash_password(pw)
        elif args.cmd in ("disable", "role"):
            losing = u.role == "admin" and u.active and (args.cmd == "disable" or args.role != "admin")
            if losing and auth.active_admins(db) <= 1:
                raise SystemExit("Tidak bisa: harus selalu ada minimal satu admin aktif")
            if args.cmd == "disable":
                u.active = False
            else:
                u.role = args.role
        elif args.cmd == "enable":
            u.active = True
        db.add(u)
        auth.audit(db, f"user_{args.cmd}", "cli", u.username, commit=False)
        db.commit()
        auth.revoke_sessions(db, u.username)
        print("Selesai. Sesi login pengguna ini diakhiri.")


if __name__ == "__main__":
    main()
