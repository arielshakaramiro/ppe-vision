"""Akun, sesi login, dan peran.

Peran (dari terendah):
- viewer     : melihat Live, Analitik, Evidence, Sistem, Bisnis, laporan, dan bertanya ke agent
- supervisor : + me-review evidence, mencatat/menghapus intervensi
- admin      : + mengganti sumber kamera, mengelola akun

Sesi memakai cookie HttpOnly + SameSite=Strict. Browser mengirimnya otomatis juga untuk <img>,
<video>, dan stream MJPEG, sehingga foto evidence ikut terlindungi tanpa token di URL.

Mode lokal: jika belum ada satu pun akun, server hanya melayani request dari komputer server sendiri
(dianggap admin "lokal") dan menolak perangkat lain. Ini supaya instalasi baru tidak terbuka ke jaringan.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session, delete, select

from . import config
from .db import AuditLog, LoginFailure, SessionToken, User, engine
from .queries import now_local

log = logging.getLogger("ppe.auth")

ROLES = ("viewer", "supervisor", "admin")
LEVEL = {r: i for i, r in enumerate(ROLES)}
COOKIE = "ppe_session"
LOCAL_HOSTS = {"127.0.0.1", "::1", "testclient"}
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
MIN_PASSWORD = 10

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


@dataclass
class CurrentUser:
    username: str
    role: str
    local: bool = False   # True = mode lokal tanpa akun


# ---------- password ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=32, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


# Kata dasar yang paling sering dipakai sebagai password (setelah angka dan simbol dibuang).
_WEAK_WORDS = {
    "password", "passw", "pass", "qwerty", "qwertyuiop", "asdfgh", "asdfghjkl", "zxcvbnm", "admin", "administrator",
    "welcome", "letmein", "iloveyou", "monkey", "dragon", "master", "login", "user", "root", "test", "guest",
    "abc", "abcdef", "abcdefg", "abcdefgh", "rahasia", "sayang", "bismillah", "indonesia", "jakarta", "merdeka",
    "ppeguard", "apdvision", "apd", "ppe", "safety", "helm", "supervisor", "viewer", "operator", "kamera",
}


def password_problem(username: str, password: str) -> str | None:
    """Alasan password ditolak, atau None jika boleh dipakai."""
    if len(password) < MIN_PASSWORD:
        return f"Password minimal {MIN_PASSWORD} karakter"
    if len(password) > 200:
        return "Password terlalu panjang"
    low = password.lower()
    letters = re.sub(r"[^a-z]", "", low)
    if len(set(password)) < 4:
        return "Password terlalu mudah ditebak: karakternya terlalu sedikit variasi"
    if username and username.lower() in low:
        return "Password tidak boleh memuat username"
    if not letters or letters in _WEAK_WORDS:
        return "Password terlalu mudah ditebak: hindari kata umum seperti password, admin, qwerty, atau deretan angka"
    if low in "0123456789012345678901234567890" or low in "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz":
        return "Password terlalu mudah ditebak: jangan memakai deret berurutan"
    return None


def validate_new_account(username: str, password: str | None, role: str | None) -> None:
    if not USERNAME_RE.match(username or ""):
        raise ValueError("Username 3–32 karakter: huruf kecil, angka, titik, garis bawah, atau strip")
    if password is not None:
        problem = password_problem(username, password)
        if problem:
            raise ValueError(problem)
    if role is not None and role not in ROLES:
        raise ValueError(f"Peran harus salah satu dari: {', '.join(ROLES)}")


# ---------- akun ----------

def has_accounts(db: Session) -> bool:
    return db.exec(select(User).where(User.active == True).limit(1)).first() is not None  # noqa: E712


def create_user(db: Session, username: str, password: str, role: str, actor: str | None = None) -> User:
    """Buat akun. Jika `actor` diisi, jejak audit `user_dibuat` disimpan dalam commit yang sama."""
    username = username.strip().lower()
    validate_new_account(username, password, role)
    if db.exec(select(User).where(User.username == username)).first():
        raise ValueError("Username sudah dipakai")
    u = User(username=username, password_hash=hash_password(password), role=role, created_at=now_local())
    db.add(u)
    if actor:
        audit(db, "user_dibuat", actor, f"{username} ({role})", commit=False)
    db.commit()
    db.refresh(u)
    return u


def active_admins(db: Session) -> int:
    return len(db.exec(select(User).where((User.role == "admin") & (User.active == True))).all())  # noqa: E712


def revoke_sessions(db: Session, username: str) -> None:
    db.exec(delete(SessionToken).where(SessionToken.username == username))
    db.commit()


def bootstrap_admin() -> None:
    """Buat admin awal dari DASHBOARD_USER/DASHBOARD_PASSWORD jika belum ada."""
    user, password = config.DASHBOARD_USER.strip().lower(), config.DASHBOARD_PASSWORD
    if not (user and password):
        return
    with Session(engine) as db:
        if db.exec(select(User).where(User.username == user)).first():
            return
        try:
            create_user(db, user, password, "admin", actor="sistem")
            log.info("Admin awal '%s' dibuat dari .env. Hapus DASHBOARD_PASSWORD dari .env setelah ini.", user)
        except ValueError as e:
            log.error("Admin awal tidak dibuat: %s", e)


# ---------- sesi ----------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = now_local()
    db.add(SessionToken(token_hash=_token_hash(token), username=username, created_at=now,
                        expires_at=now + timedelta(hours=config.SESSION_HOURS)))
    db.exec(delete(SessionToken).where(SessionToken.expires_at < now))  # bersihkan sesi kedaluwarsa
    db.commit()
    return token


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.exec(delete(SessionToken).where(SessionToken.token_hash == _token_hash(token)))
        db.commit()


def user_from_token(db: Session, token: str | None) -> CurrentUser | None:
    if not token or len(token) > 200:
        return None
    s = db.exec(select(SessionToken).where(SessionToken.token_hash == _token_hash(token))).first()
    if not s or s.expires_at < now_local():
        return None
    u = db.exec(select(User).where(User.username == s.username)).first()
    if not u or not u.active:
        return None
    return CurrentUser(u.username, u.role)


def authenticate(db: Session, username: str, password: str) -> User | None:
    u = db.exec(select(User).where(User.username == username.strip().lower())).first()
    if not u or not u.active:
        verify_password(password, _DUMMY_HASH)  # samakan waktu respons: tidak membocorkan username mana yang ada
        return None
    if not verify_password(password, u.password_hash):
        return None
    u.last_login = now_local()
    db.add(u)
    db.commit()
    return u


def audit(db: Session, action: str, actor: str, detail: str = "", violation_id: int | None = None,
          commit: bool = True) -> None:
    """Catat aksi. commit=False dipakai saat perubahan data dan audit-nya harus tersimpan dalam satu transaksi."""
    db.add(AuditLog(at=now_local(), action=action, actor=actor[:64], detail=detail[:300], violation_id=violation_id))
    if commit:
        db.commit()


# ---------- pembatas login gagal ----------

class LoginThrottle:
    """Pembatas login gagal yang tersimpan di database.

    Tiga lapis, semuanya dalam jendela waktu yang sama (bawaan 15 menit):
    - pasangan IP + username (bawaan 5): menghentikan tebak-tebakan password satu akun dari satu perangkat.
      Hanya pasangan ini yang terkunci, jadi orang lain tidak bisa mengunci akun admin dari perangkatnya sendiri.
    - per IP (bawaan 20): menghentikan satu perangkat yang mencoba banyak username.
    - per username dari semua IP (bawaan 50): menghentikan tebakan terdistribusi ke satu akun. Batasnya sengaja
      tinggi supaya mengunci akun orang lain butuh banyak alamat IP.
    """

    def __init__(self) -> None:
        cfg = config.load_config().get("login_throttle", {})
        self.window = timedelta(minutes=float(cfg.get("window_min", 15)))
        self.limits = {"pair": int(cfg.get("per_ip_user", 5)), "ip": int(cfg.get("per_ip", 20)),
                       "user": int(cfg.get("per_user", 50))}

    @staticmethod
    def _keys(ip: str, user: str) -> dict[str, str]:
        return {"pair": f"pair:{ip}|{user}"[:200], "ip": f"ip:{ip}"[:200], "user": f"user:{user}"[:200]}

    def blocked(self, ip: str, user: str) -> bool:
        since = now_local() - self.window
        with Session(engine) as db:
            for kind, key in self._keys(ip, user).items():
                if self.limits[kind] <= 0:
                    continue  # 0 = lapis ini dimatikan
                rows = db.exec(select(LoginFailure.id).where((LoginFailure.key == key) & (LoginFailure.at >= since))
                               .limit(self.limits[kind])).all()
                if len(rows) >= self.limits[kind]:
                    return True
        return False

    def fail(self, ip: str, user: str) -> None:
        from sqlalchemy import text
        now = now_local()
        since = now - self.window
        with Session(engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            for kind, key in self._keys(ip, user).items():
                if self.limits[kind] <= 0:
                    continue
                rows = db.exec(select(LoginFailure.id).where((LoginFailure.key == key) & (LoginFailure.at >= since)).limit(self.limits[kind])).all()
                if len(rows) >= self.limits[kind]:
                    return  # Sudah melebihi batas, jangan tambah lagi
            for key in self._keys(ip, user).values():
                db.add(LoginFailure(key=key, at=now))
            db.exec(delete(LoginFailure).where(LoginFailure.at < now - self.window))
            db.commit()

    def reset(self, ip: str, user: str) -> None:
        """Login berhasil: hapus hitungan pasangan ini saja (hitungan IP dan username tetap)."""
        with Session(engine) as db:
            db.exec(delete(LoginFailure).where(LoginFailure.key == self._keys(ip, user)["pair"]))
            db.commit()

    def clear(self) -> None:
        with Session(engine) as db:
            db.exec(delete(LoginFailure))
            db.commit()


throttle = LoginThrottle()


# ---------- 2FA (TOTP) ----------

def generate_totp_secret() -> str:
    """Buat secret base32 acak (16 karakter)."""
    return base64.b32encode(secrets.token_bytes(10)).decode("utf-8").strip("=")

def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """Verifikasi kode 6 digit TOTP dari Authenticator."""
    if not secret or not code or len(code) != 6 or not code.isdigit():
        return False
    try:
        secret = secret + "=" * ((8 - len(secret) % 8) % 8)
        key = base64.b32decode(secret, casefold=True)
    except Exception:
        return False

    current = int(time.time() / 30)
    for i in range(-window, window + 1):
        msg = struct.pack(">Q", current + i)
        h = hmac.new(key, msg, hashlib.sha1).digest()
        o = h[19] & 15
        val = (struct.unpack(">I", h[o:o+4])[0] & 0x7fffffff) % 1000000
        if f"{val:06d}" == code:
            return True
    return False
