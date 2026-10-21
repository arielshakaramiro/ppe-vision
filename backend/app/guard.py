"""Pembatas cakupan agent.

Agent hanya boleh menjawab pertanyaan tentang data kepatuhan APD di sistem ini.
Penjagaan berlapis, karena satu lapis saja mudah ditembus:

1. check_input  : aturan deterministik SEBELUM LLM dipanggil. Menolak upaya membobol sistem
                  (prompt injection, peretasan, minta secret), permintaan coding, dan topik di luar K3/APD.
                  Pertanyaan yang ditolak di sini tidak pernah sampai ke LLM (hemat biaya, tidak bisa dibujuk).
2. system prompt: LLM diberi batas tegas dan kalimat penolakan baku (lihat agent.system_prompt).
3. tool         : LLM hanya punya tool baca-saja; tidak ada SQL, file, atau shell.
4. check_output : jawaban LLM diperiksa lagi; jawaban berisi kode atau bocoran instruksi diganti penolakan.

Daftar kata di sini sengaja tidak ditampilkan ke pengguna. Pesan penolakan dibuat umum
supaya tidak mengajari cara mengakalinya.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

REFUSE_GENERAL = (
    "Maaf, permintaan itu tidak bisa saya proses. Saya hanya menjawab pertanyaan tentang "
    "data kepatuhan APD di sistem APD Vision."
)
REFUSE_SCOPE = (
    "Maaf, saya asisten data K3 untuk APD Vision, jadi hanya bisa menjawab pertanyaan tentang "
    "kepatuhan APD (helm, rompi, sarung tangan, sepatu) dari kamera di sistem ini.\n"
    "Contoh: \"Berapa orang tanpa helm hari ini?\", \"Tunjukkan pelanggaran sarung tangan kemarin\", "
    "atau \"Rangkum kepatuhan 7 hari terakhir\"."
)
GREETING_REPLY = (
    "Halo! Saya bisa menjawab pertanyaan tentang data kepatuhan APD dari kamera, misalnya jumlah "
    "pelanggaran, APD yang paling sering dilupakan, jam rawan, dan foto evidence-nya."
)
HELP_REPLY = (
    "Anda bisa menanyakan data kepatuhan APD dari kamera, misalnya: \"Berapa orang tanpa helm hari ini?\", "
    "\"Tunjukkan evidence tanpa rompi di LiveCAM-01\", \"Rangkum kepatuhan 7 hari terakhir\", atau "
    "\"Jam berapa pelanggaran paling sering terjadi?\""
)

# --- normalisasi: mempersulit pengelabuan dengan huruf aneh, spasi, atau angka pengganti huruf ---
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).translate(_ZERO_WIDTH)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", text).strip()


def _deleet(text: str) -> str:
    # angka pengganti huruf hanya dibalik untuk pencocokan pola serangan, bukan untuk "cam-02" dsb.
    return text.translate(_LEET)


def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_INJECTION = _rx(
    r"\b(abaikan|lupakan|hiraukan|ignore|disregard|forget)\b.{0,30}\b(instruksi|aturan|perintah|batasan|prompt|"
    r"instructions?|rules?|previous|above|sebelumnya)\b",
    r"system ?prompt", r"prompt sistem", r"instruksi (sistem|awal|rahasia)", r"pesan sistem",
    r"\b(kamu|anda) (sekarang|adalah sekarang) (adalah|jadi|menjadi)\b", r"\byou are now\b", r"\bact as\b",
    r"\b(berperan|berpura[- ]pura|pura[- ]pura) (sebagai|jadi|menjadi)\b", r"\bpretend\b", r"\broleplay\b",
    r"\b(developer|dev|god|admin) ?mode\b", r"\bmode (developer|admin|bebas)\b", r"\bjailbreak\b", r"\bdan mode\b",
    r"tanpa (batasan|filter|sensor)", r"\bno (restrictions?|limits?|filters?)\b", r"\bunfiltered\b",
    r"(tampilkan|tunjukkan|bocorkan|sebutkan|ulangi|print|reveal|show|repeat).{0,25}(instruksi|prompt|aturan(mu| kamu)|"
    r"konfigurasi|instructions?|rules)",
    r"<\|", r"\[/?inst\]", r"</?(system|assistant|user)>", r"^\s*(system|assistant)\s*:",
)

_ATTACK = _rx(
    r"\b(hack|hacking|retas|meretas|bobol|membobol|jebol|exploit|eksploit|crack)\w*",
    r"\b(sql|xss|command|code|prompt) ?inje[ck]\w*", r"\binjeksi\b", r"\bpayload\b", r"\b(reverse )?shell\b",
    r"\brce\b", r"\bbypass\w*", r"\bprivilege\b", r"\bsudo\b", r"\broot access\b", r"\bbackdoor\b", r"\bmalware\b",
    r"rm -rf", r"\bdrop (table|database)\b", r"\bunion select\b", r"\bselect \* from\b", r"\bdelete from\b",
    r"\.\./", r"/etc/(passwd|shadow)", r"\.env\b", r"\bapi ?key\b", r"\bapikey\b", r"\btoken\b", r"\bsecret\b",
    r"\bpassword\b", r"\bkata sandi\b", r"\bsandi\b", r"\bkredensial\b", r"\bcredentials?\b",
    r"\bbrute ?force\b", r"\bddos\b", r"\bphishing\b", r"\bkeylogger\b",
    r"(hapus|ubah|edit|ganti|update|delete|insert|modify|manipulasi).{0,25}(database|tabel|data pelanggaran|record|"
    r"evidence|bukti|log)",
)

_CODING = _rx(
    r"\b(coding|ngoding|koding|kodingan|programming|pemrograman)\b", r"\b(kode|code) (program|sumber|python|js)\b",
    r"\bsource ?code\b", r"\b(script|skrip)\b", r"\bpython\b", r"\bjavascript\b", r"\btypescript\b", r"\bjava\b",
    r"\bphp\b", r"\bgolang\b", r"\brust\b", r"\bc\+\+", r"\bkotlin\b", r"\bswift\b", r"\bhtml\b", r"\bcss\b",
    r"\bsql\b", r"\bregex\b", r"\bbash\b", r"\bpowershell\b", r"\bterminal\b", r"\bcommand line\b",
    r"\b(debug|debugging|compile|kompilasi|refactor)\b", r"\b(npm|pip|docker|git|github)\b",
    r"\b(buat|buatkan|tuliskan|tulis|bikin|bikinin)\b.{0,20}\b(program|aplikasi|fungsi|function|class|website|web|bot)\b",
    r"```", r"\bdef \w+\(", r"\bimport \w+", r"\bconsole\.log\b", r"\bfunction\s*\(",
)

# kata yang menunjukkan pertanyaan memang tentang domain sistem ini
_DOMAIN = _rx(
    r"\bapd\b", r"\bppe\b", r"\bhelm\w*", r"\brompi\b", r"\bvest\b", r"sarung tangan", r"\bglove\w*", r"\bsepatu\b",
    r"\bboots?\b", r"langgar", r"\bpatuh\w*", r"kepatuhan", r"\bcompliance\b", r"\bcompliant\b", r"\bk3\b", r"\bhse\b",
    r"\bsafety\b", r"keselamatan", r"\bpekerja\b", r"\bkaryawan\b", r"\bworker\b", r"\bkamera\b", r"\bcam-?\d*\b",
    r"\bcctv\b", r"\bzona\b", r"\barea\b", r"\bgudang\b", r"\bproduksi\b", r"loading dock", r"\bevidence\b",
    r"\bbukti\w*", r"\bfoto(nya)?\b", r"\bgambar(nya)?\b", r"\breview\b", r"false positive", r"\blaporan\b", r"\brangkum\w*",
    r"\bringkas\w*", r"\bstatistik\b", r"\btren\b", r"jam rawan", r"\binsiden\b", r"\bkejadian\b", r"tidak pasti",
    r"\balert\b", r"\bw\d{3}\b", r"\btanpa (helm|rompi|sepatu|sarung)", r"\bpaparan\b", r"\bshift\b",
    r"\bintervensi\b", r"\bbriefing\b", r"\brisiko\b",
    r"\bregulasi\b", r"\baturan\b", r"\bpermenaker\b", r"\bundang[- ]?undang\b", r"\buu\b", r"\bpasal\b",
    r"\bsop\b", r"\bstandar\b", r"\bsni\b", r"\bhukum\b", r"\blandasan\b",
)
# kata waktu: cukup untuk pertanyaan lanjutan pendek, misalnya "kalau kemarin?"
_TIME = _rx(
    r"\bhari ini\b", r"\bkemarin\b", r"\bminggu\b", r"\bbulan\b", r"\bpagi\b", r"\bsiang\b", r"\bsore\b",
    r"\bmalam\b", r"\bjam \d", r"\b\d{1,2}[.:]\d{2}\b", r"\btanggal\b", r"\bperiode\b", r"\bterakhir\b",
    r"\b\d+ hari\b", r"\bseminggu\b", r"\bsebulan\b",
)
_FOLLOW_UP = _rx(r"\b(itu|tadi|tersebut|lagi|lainnya|yang lain|detail\w*|rinci\w*|lanjut\w*|kalau|bagaimana dengan)\b")
_GREETING = _rx(
    r"^(halo|hai|hi|hello|hey|selamat (pagi|siang|sore|malam)|assalamualaikum|terima ?kasih|makasih|thanks?|ok|oke|sip)"
    r"[\s!.,?]*$",
    r"^(kamu|anda) bisa (apa|bantu apa)", r"^(apa|bisa) (yang )?(bisa )?(kamu|anda) (lakukan|bantu)",
    r"^(apa )?yang bisa saya tanyakan", r"^saya bisa (tanya|menanyakan) apa", r"^contoh pertanyaan",
)


@dataclass
class GuardResult:
    allowed: bool
    category: str | None = None   # injection | attack | coding | out_of_scope | greeting
    reply: str | None = None      # jawaban baku jika LLM tidak perlu dipanggil


def check_input(message: str, has_history: bool = False) -> GuardResult:
    text = normalize(message)
    if not text:
        return GuardResult(False, "out_of_scope", REFUSE_SCOPE)
    probe = f"{text} {_deleet(text)}"
    if _INJECTION.search(probe):
        return GuardResult(False, "injection", REFUSE_GENERAL)
    if _ATTACK.search(probe):
        return GuardResult(False, "attack", REFUSE_GENERAL)
    if _CODING.search(probe):
        return GuardResult(False, "coding", REFUSE_SCOPE)
    if _GREETING.search(text):
        reply = HELP_REPLY if re.search(r"tanyakan|tanya|contoh pertanyaan", text) else GREETING_REPLY
        return GuardResult(True, "greeting", reply)
    if _DOMAIN.search(text) or _TIME.search(text) or (has_history and _FOLLOW_UP.search(text)):
        return GuardResult(True)
    return GuardResult(False, "out_of_scope", REFUSE_SCOPE)


_BAD_OUTPUT = _rx(
    r"```", r"\bdef \w+\(", r"\bimport \w+", r"\bselect .+ from\b", r"<script", r"\bfunction\s*\w*\s*\(",
    r"system ?prompt", r"instruksi sistem", r"\binput_schema\b", r"anthropic_api_key", r"telegram_bot_token",
)
MAX_ANSWER = 1500


def check_output(answer: str) -> str:
    """Jawaban LLM yang keluar jalur (berisi kode, bocoran instruksi) diganti penolakan baku."""
    if _BAD_OUTPUT.search(normalize(answer)):
        return REFUSE_GENERAL
    if len(answer) > MAX_ANSWER:
        answer = answer[:MAX_ANSWER].rsplit(" ", 1)[0] + " …"
    return answer
