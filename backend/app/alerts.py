"""Alert Telegram: foto evidence + ringkasan pelanggaran, dengan tombol review langsung dari chat."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("ppe.alerts")
LABEL = {"helmet": "helm", "vest": "rompi", "gloves": "sarung tangan", "shoes": "sepatu"}


def telegram_enabled() -> bool:
    enabled_in_config = config.load_config().get("alerts", {}).get("telegram", True)
    return bool(enabled_in_config and config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


class Cooldown:
    """Mencegah banjir alert saat status satu pekerja naik-turun berulang kali."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._last: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def ready(self, key: tuple) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.seconds:
                return False
            self._last[key] = now
            if len(self._last) > 5000:  # buang entri lama supaya memori tidak tumbuh terus
                self._last = {k: t for k, t in self._last.items() if now - t < self.seconds}
            return True


_cooldown = Cooldown(config.ALERT_COOLDOWN_S)
_camera_cooldown = Cooldown(float(config.load_config().get("quality", {}).get("alert_cooldown_s", 3600)))


def telegram_call(method: str, data: dict | None = None, files: dict | None = None, timeout: float = 15):
    """Panggil Bot API. Tidak pernah mencetak URL (berisi token) ke log."""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    try:
        r = httpx.post(url, data=data, files=files, timeout=timeout)
        if r.status_code != 200:
            log.warning("Telegram %s ditolak (HTTP %s)", method, r.status_code)
            return None
        return r.json().get("result")
    except Exception as e:  # alert tidak boleh menghentikan pipeline
        log.warning("Gagal memanggil Telegram %s: %s", method, type(e).__name__)
        return None


def review_keyboard(event_id: int) -> str:
    return json.dumps({"inline_keyboard": [[
        {"text": "✅ Benar pelanggaran", "callback_data": f"rv:{event_id}:c"},
        {"text": "❌ False positive", "callback_data": f"rv:{event_id}:fp"},
    ]]})


def _send_photo(photo: Path, caption: str, event_id: int | None) -> None:
    data: dict[str, object] = {"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    thread_id = getattr(config, "TELEGRAM_ALERT_THREAD_ID", "")
    if thread_id:
        try:
            data["message_thread_id"] = int(thread_id)
        except ValueError:
            pass
    if event_id is not None:
        data["reply_markup"] = review_keyboard(event_id)
    try:
        with open(photo, "rb") as f:
            telegram_call("sendPhoto", data, files={"photo": f})
    except OSError as e:
        log.warning("Foto evidence tidak bisa dibaca: %s", type(e).__name__)


def send_violation_alert(photo: Path, camera_id: str, zone: str, worker_id: int, when, missing: list[str],
                         event_id: int | None = None) -> bool:
    """Kirim alert di thread terpisah. Return True jika alert benar-benar dijadwalkan."""
    if not telegram_enabled() or not _cooldown.ready((camera_id, worker_id)):
        return False
    kurang = ", ".join(LABEL.get(m, m) for m in missing) or "-"
    caption = (
        f"🚨 <b>PELANGGARAN APD TERDETEKSI</b>\n\n"
        f"• <b>Pekerja:</b> W{worker_id:03d}\n"
        f"• <b>Kamera:</b> {camera_id} | <b>Zona:</b> {zone}\n"
        f"• <b>Waktu:</b> {when:%d-%m-%Y %H:%M:%S} WIB\n"
        f"• <b>APD Kurang:</b> <b>{kurang}</b>\n\n"
        f"<i>Tekan tombol di bawah untuk verifikasi pengawas:</i>"
    )
    threading.Thread(target=_send_photo, args=(photo, caption, event_id), daemon=True).start()
    return True


def send_camera_alert(camera_id: str, issues: list[str]) -> bool:
    if not telegram_enabled() or not _camera_cooldown.ready(("camera", camera_id)):
        return False
    text = f"KUALITAS KAMERA {camera_id} MENURUN\n" + "\n".join(f"- {i}" for i in issues)
    data: dict[str, object] = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text}
    thread_id = getattr(config, "TELEGRAM_ALERT_THREAD_ID", "")
    if thread_id:
        try:
            data["message_thread_id"] = int(thread_id)
        except ValueError:
            pass
    threading.Thread(target=telegram_call, args=("sendMessage", data),
                     daemon=True).start()
    return True
