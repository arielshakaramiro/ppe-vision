"""Pengelolaan kamera: enkripsi URL (Fernet) dan penyamaran untuk frontend.

Kunci Fernet dari `CAMERA_SECRET_KEY` di `.env` (bukan di database). Tanpa kunci, URL kamera
tidak bisa dibuka lagi. Response/log/audit hanya memuat bentuk tersamar `rtsp://***@host/path`.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

from .config import CAMERA_SECRET_KEY

log = logging.getLogger("ppe.camera_store")


def _fernet():
    from cryptography.fernet import Fernet
    if not CAMERA_SECRET_KEY:
        raise RuntimeError("CAMERA_SECRET_KEY belum di-set di .env; URL kamera tidak bisa dienkripsi.")
    return Fernet(CAMERA_SECRET_KEY.encode())


def encrypt_url(url: str) -> str:
    return _fernet().encrypt(url.encode()).decode()


def decrypt_url(token: str) -> str:
    if not token:
        return ""
    return _fernet().decrypt(token.encode()).decode()


def mask_url(url: str) -> str:
    """Samarkan kredensial: rtsp://user:pass@host/path -> rtsp://***@host/path."""
    if not url:
        return ""
    if "://" not in url:
        return url
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    cred = "***@" if parts.username else ""
    return f"{parts.scheme}://{cred}{host}{parts.path}"


def masked_from_enc(token: str) -> str:
    """Bentuk tersamar dari token terenkripsi, tanpa membocorkan URL utuh."""
    try:
        return mask_url(decrypt_url(token))
    except Exception:
        return ""
