"""Utilitas keamanan: redaksi pesan error, rate limit sederhana, validasi URL kamera."""
from __future__ import annotations

import re
import threading
import time
from collections import deque

_CREDENTIALS_IN_URL = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@")
_TELEGRAM_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_API_KEY = re.compile(r"\b(sk-[A-Za-z0-9_-]{6,})")


def redact(text: str) -> str:
    """Hapus kredensial dari teks yang mungkin sampai ke browser atau log."""
    text = _CREDENTIALS_IN_URL.sub(r"\1***@", text)
    text = _TELEGRAM_TOKEN.sub("bot***", text)
    return _API_KEY.sub("sk-***", text)


class RateLimiter:
    """Sliding window per kunci (misalnya IP). Cukup untuk satu proses."""

    def __init__(self, max_calls: int, per_seconds: float = 60.0):
        self.max_calls, self.per = max_calls, per_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.max_calls <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.per:
                q.popleft()
            if len(q) >= self.max_calls:
                return False
            q.append(now)
            if len(self._hits) > 5000:  # buang kunci yang sudah tidak aktif
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] <= self.per}
            return True


def validate_stream_url(url: str) -> str:
    """Validasi URL kamera yang dimasukkan lewat dashboard.

    Server akan membuka koneksi ke URL ini, jadi URL dibatasi supaya fitur ini tidak bisa dipakai
    untuk menjangkau layanan internal server sendiri (SSRF): hanya rtsp/http/https, dan alamat
    loopback, link-local (termasuk metadata cloud 169.254.169.254), multicast, atau 0.0.0.0 ditolak.
    Alamat LAN privat (192.168.x.x) tetap diizinkan karena kamera/HP demo biasanya di jaringan lokal.
    """
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    url = (url or "").strip()
    if not url or len(url) > 500 or any(c.isspace() for c in url):
        raise ValueError("URL kamera kosong, terlalu panjang, atau mengandung spasi")
    parts = urlsplit(url)
    if parts.scheme not in ("rtsp", "http", "https"):
        raise ValueError("URL kamera harus diawali rtsp://, http://, atau https://")
    if not parts.hostname:
        raise ValueError("URL kamera tidak memiliki host")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 554, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise ValueError("Host kamera tidak ditemukan") from None
    safe_ip = ""
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise ValueError("Alamat kamera ini tidak diizinkan")
        safe_ip = str(ip)

    auth_part = f"{parts.username}{':' + parts.password if parts.password else ''}@" if parts.username else ""
    port_part = f":{parts.port}" if parts.port else ""
    netloc = f"{auth_part}{safe_ip}{port_part}"
    url = parts._replace(netloc=netloc).geturl()

    if parts.scheme in ("http", "https"):
        import httpx
        try:
            r = httpx.head(url, follow_redirects=False, timeout=5.0)
            if r.is_redirect and "location" in r.headers:
                return validate_stream_url(r.headers["location"])
        except Exception:
            pass

    return url


def parse_networks(spec: str) -> list:
    """'127.0.0.1, 172.16.0.0/12' -> daftar jaringan. Entri yang tidak valid diabaikan."""
    import ipaddress
    nets = []
    for part in (spec or "").split(","):
        part = part.strip()
        if part:
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                pass
    return nets


def resolve_client(peer: str | None, forwarded_for: str | None, trusted: list) -> tuple[str, bool]:
    """Alamat klien sebenarnya, dan apakah request datang lewat reverse proxy tepercaya.

    X-Forwarded-For hanya dipercaya jika koneksi langsungnya berasal dari proxy di `trusted`; header itu mudah
    dipalsukan oleh siapa pun. Daftar dibaca dari kanan, dan alamat pertama yang bukan proxy tepercaya dipakai.
    """
    import ipaddress

    def is_trusted(addr: str) -> bool:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        return any(ip in n for n in trusted)

    peer = peer or "unknown"
    if not trusted or not is_trusted(peer):
        return peer, False
    hops = [h.strip() for h in (forwarded_for or "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not is_trusted(hop):
            return hop[:64], True
    return (hops[0][:64] if hops else peer), True
