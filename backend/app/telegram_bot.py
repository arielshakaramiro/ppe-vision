"""Review evidence langsung dari Telegram.

Bot memakai long polling (getUpdates), jadi tidak butuh URL publik atau webhook: cocok untuk laptop demo.
Tombol di pesan alert mengirim callback "rv:<id>:c" atau "rv:<id>:fp". Callback hanya diterima jika
datang dari chat yang sama dengan TELEGRAM_CHAT_ID, dan (jika diisi) dari user di TELEGRAM_REVIEWER_IDS.
"""
from __future__ import annotations

import logging
import re
import threading

from sqlmodel import Session, select

from . import config
from .alerts import telegram_call
from .db import AuditLog, Violation, engine
from .queries import now_local

log = logging.getLogger("ppe.telegram")
_CALLBACK = re.compile(r"^rv:(\d{1,12}):(c|fp)$")
_LABEL = {"confirmed": "✅ Benar pelanggaran", "false_positive": "❌ False positive"}


def format_telegram_html(text: str) -> str:
    """Format teks markdown ke format HTML yang didukung Telegram Bot API.

    Menghilangkan simbol mentah seperti **, #, dan menggantinya dengan styling tebal/list yang rapi.
    """
    import html

    if not text:
        return ""
    # 1. Escape karakter HTML agar tidak rusak
    escaped = html.escape(text)

    # 2. Bold: **teks** -> <b>teks</b>
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)

    # 3. Code: `teks` -> <code>teks</code>
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)

    # 4. Headers: ### Judul -> <b>Judul</b>
    escaped = re.sub(r"(?m)^#{1,4}\s+(.+)$", r"<b>\1</b>", escaped)

    # 5. Bullet points: baris diawali - atau * menjadi • yang rapi
    escaped = re.sub(r"(?m)^[\-\*]\s+", "• ", escaped)

    # 6. Rapikan jarak spasi baris kosong berlebih
    escaped = re.sub(r"\n{3,}", "\n\n", escaped)

    return escaped.strip()


def reviewer_for(db: Session, telegram_user_id) -> tuple[bool, str]:
    """Siapa yang boleh menekan tombol review, dan dicatat sebagai siapa.

    Hanya boleh jika akun dashboard yang terhubung ke ID Telegram ini aktif dan
    berperan supervisor/admin. Dicatat atas nama username akun tersebut.
    """
    from .auth import LEVEL
    from .db import User

    tid = str(telegram_user_id or "")
    if tid:
        linked = db.exec(select(User).where(User.telegram_id == tid)).first()
        if linked:
            ok = linked.active and LEVEL.get(linked.role, 0) >= LEVEL["supervisor"]
            return ok, linked.username
    return False, f"telegram:{tid or '?'}"


def handle_callback(cb: dict) -> str:
    """Proses satu callback_query. Mengembalikan teks singkat untuk ditampilkan ke user Telegram."""
    message = cb.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    user = cb.get("from") or {}
    if chat_id != str(config.TELEGRAM_CHAT_ID) and chat.get("type") != "private":
        log.warning("Callback Telegram dari chat tidak dikenal ditolak")
        return "Chat ini tidak berwenang."
    with Session(engine) as db:
        allowed, actor = reviewer_for(db, user.get("id"))
    if not allowed:
        return "Anda tidak terdaftar sebagai reviewer."
    m = _CALLBACK.match(str(cb.get("data", "")))
    if not m:
        return "Perintah tidak dikenal."
    event_id, choice = int(m.group(1)), m.group(2)
    review = "confirmed" if choice == "c" else "false_positive"
    linked = not actor.startswith("telegram:")
    # Untuk audit dipakai ID Telegram (tetap), bukan username Telegram yang bisa diganti pemiliknya.
    who = actor if linked else (user.get("username") or str(user.get("id", "?")))
    audit_actor = actor
    with Session(engine) as db:
        row = db.get(Violation, event_id)
        if not row:
            return "Event tidak ditemukan."
        previous = row.review
        now = now_local()
        row.review, row.reviewed_at = review, now
        if review == "false_positive" and not row.corrected_items:
            row.corrected_items = row.missing_items
        db.add(row)
        db.add(AuditLog(at=now, action=f"review_{review}", violation_id=event_id, actor=audit_actor[:64],
                        detail=f"sebelumnya: {previous}; via Telegram oleh {who}"[:300]))
        db.commit()
    # ganti tombol dengan hasil review supaya reviewer lain tahu sudah ditangani
    if message.get("message_id"):
        caption = (message.get("caption") or "") + f"\n\n<b>Hasil Review:</b> {_LABEL[review]} oleh <b>{who}</b>"
        telegram_call("editMessageCaption", data={"chat_id": chat_id, "message_id": message["message_id"],
                                                  "caption": caption[:1024], "parse_mode": "HTML"})
    return f"Tersimpan: {_LABEL[review]}"


def handle_message(msg: dict) -> None:
    """Proses pesan teks dari Telegram dan balas dengan jawaban Agent K3."""
    from . import agent
    from .config import EVIDENCE_DIR

    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    chat_type = chat.get("type", "private")
    thread_id = msg.get("message_thread_id")
    text = (msg.get("text") or "").strip()
    user = msg.get("from") or {}
    user_id = user.get("id")

    if not text:
        return

    log.info("Pesan Telegram: chat_id=%s, thread_id=%s, user=%s: %s", chat_id, thread_id, user_id, text[:60])

    with Session(engine) as db:
        allowed, actor = reviewer_for(db, user_id)

    is_group = chat_type in ("group", "supergroup", "channel") or chat_id.startswith("-")
    if is_group:
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return
        # Di grup/topic, proses jika ada di topic non-alert atau diawali perintah/mention
        alert_thread = getattr(config, "TELEGRAM_ALERT_THREAD_ID", "")
        if alert_thread and str(thread_id or "") == str(alert_thread):
            # Jangan respon chat biasa di dalam topic khusus alert foto
            if not text.startswith("/"):
                return
    else:
        # Chat pribadi (DM)
        if chat_id != str(config.TELEGRAM_CHAT_ID) and not allowed:
            telegram_call("sendMessage", {"chat_id": chat_id, "text": "Akses ditolak: ID Telegram Anda belum ditautkan ke akun APD Vision."})
            return

    def reply_tg(method: str, payload: dict, files: dict | None = None):
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        return telegram_call(method, data=payload, files=files)

    if text.startswith(("/start", "/bantuan", "/help")):
        welcome = (
            "👋 <b>Halo! Saya Asisten AI K3 APD Vision.</b>\n\n"
            "Anda dapat menanyakan data kepatuhan APD, statistik kamera CCTV, maupun regulasi keselamatan kerja (Permenaker & UU K3).\n\n"
            "💡 <b>Contoh pertanyaan:</b>\n"
            "• Berapa pelanggaran APD hari ini?\n"
            "• Mengapa pekerja di area loading dock wajib pakai helm menurut regulasi?\n"
            "• Apakah pengusaha wajib memberikan APD secara gratis menurut permenaker?\n"
            "• Decision apa yang harus dilakukan agar pelanggar berkurang?"
        )
        reply_tg("sendMessage", {"chat_id": chat_id, "text": welcome, "parse_mode": "HTML"})
        return

    if text.startswith("/tanya "):
        text = text[7:].strip()

    # Kirim status "typing" ke Telegram
    reply_tg("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    username = actor if not actor.startswith("telegram:") else "telegram"
    try:
        reply = agent.chat(text, username=username)
        ans = reply.get("answer", "").strip()
        if not ans:
            ans = "Maaf, sistem tidak menghasilkan jawaban untuk pertanyaan ini."

        html_ans = format_telegram_html(ans)
        res = reply_tg("sendMessage", {"chat_id": chat_id, "text": html_ans[:4000], "parse_mode": "HTML"})
        if res is None:
            # Fallback jika ada entitas HTML yang gagal diparsing: kirim teks bersih tanpa simbol markdown
            clean_plain = re.sub(r"\*\*(.+?)\*\*", r"\1", ans)
            clean_plain = re.sub(r"(?m)^#{1,4}\s+", "", clean_plain)
            clean_plain = re.sub(r"(?m)^[\-\*]\s+", "• ", clean_plain)
            reply_tg("sendMessage", {"chat_id": chat_id, "text": clean_plain[:4000]})

        # Jika ada evidence foto terkait, kirimkan foto buktinya
        evidence = reply.get("evidence") or []
        if evidence:
            for ev_item in evidence[:2]:
                photo_file = ev_item.get("foto") or ev_item.get("foto_crop") or ev_item.get("foto_full")
                if photo_file:
                    rel_name = photo_file.lstrip("/").replace("evidence/", "", 1)
                    photo_path = EVIDENCE_DIR / rel_name
                    if photo_path.is_file():
                        wid = ev_item.get("worker_id") or ev_item.get("track_id", 0)
                        kamera = ev_item.get("kamera", "-")
                        zona = ev_item.get("zona", "-")
                        kurang = ", ".join(ev_item.get("apd_kurang", [])) or "APD tidak lengkap"
                        waktu = str(ev_item.get("waktu", "")).replace("T", " ")
                        caption = (
                            f"📸 <b>Bukti Foto CCTV (Pekerja W{wid:03d})</b>\n\n"
                            f"• <b>Kamera:</b> {kamera} | <b>Zona:</b> {zona}\n"
                            f"• <b>APD Kurang:</b> <b>{kurang}</b>\n"
                            f"• <b>Waktu:</b> {waktu} WIB"
                        )
                        try:
                            with open(photo_path, "rb") as f:
                                reply_tg("sendPhoto", {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}, files={"photo": f})
                        except Exception as err:
                            log.warning("Gagal mengirim foto evidence ke Telegram: %s", err)
    except Exception as e:
        log.error("Gagal memproses pesan Agent dari Telegram: %s", e)
        reply_tg("sendMessage", {"chat_id": chat_id, "text": "Maaf, terjadi kendala saat memproses pertanyaan Anda."})


class TelegramPoller:
    def __init__(self):
        self._stop = threading.Event()
        self._offset = 0

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="telegram-poller").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        delay = 2.0
        while not self._stop.is_set():
            updates = telegram_call("getUpdates", {"offset": self._offset, "timeout": 25,
                                                   "allowed_updates": '["callback_query", "message"]'}, timeout=35)
            if updates is None:  # jaringan putus atau token salah: tunggu lalu coba lagi
                self._stop.wait(delay)
                delay = min(delay * 2, 60)
                continue
            delay = 2.0
            for u in updates:
                self._offset = max(self._offset, u.get("update_id", 0) + 1)
                cb = u.get("callback_query")
                if cb:
                    try:
                        reply = handle_callback(cb)
                    except Exception as e:
                        log.error("Gagal memproses callback Telegram: %s", type(e).__name__)
                        reply = "Gagal menyimpan review."
                    telegram_call("answerCallbackQuery", {"callback_query_id": cb.get("id"), "text": reply})
                    continue

                msg = u.get("message")
                if msg:
                    try:
                        handle_message(msg)
                    except Exception as e:
                        log.error("Gagal memproses pesan teks Telegram: %s", e)
