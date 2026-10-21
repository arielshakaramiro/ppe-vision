"""Laporan K3 siap cetak (HTML → Simpan sebagai PDF dari browser).

Strukturnya mengikuti kebutuhan bukti pemantauan pada audit sistem manajemen K3 (SMK3 / ISO 45001):
ringkasan kinerja, temuan beserta bukti dan integritasnya, tindak lanjut, dan jejak audit.
Laporan ini dokumen pendukung, bukan pengganti laporan audit resmi.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from html import escape

from sqlmodel import Session, col, select

from . import queries as Q
from .config import load_config
from .db import AuditLog, Intervention

REPORT_ACTIONS = ("review_confirmed", "review_false_positive", "review_pending", "intervention_created",
                  "intervention_deleted", "retensi")

MAX_FINDINGS = 200
MAX_AUDIT = 150


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%d-%m-%Y %H:%M") if dt else "-"


def _num(v, suffix="") -> str:
    return "-" if v is None else f"{v}{suffix}"


def _img(rel: str) -> str:
    return f'<img src="/evidence/{escape(rel)}" alt="">' if rel else "-"


def build_report(db: Session, start: datetime, end: datetime) -> str:
    s = Q.summary(db, start, end)
    findings = [v for v in Q.fetch_violations(db, start, end)][:MAX_FINDINGS]
    total_findings = len(Q.fetch_violations(db, start, end))
    all_events = Q.fetch_violations(db, start, end, include_false_positive=True)
    reviewed = Counter(ev.review for ev in all_events)
    interventions = db.exec(select(Intervention).where(Intervention.at >= start, Intervention.at < end)
                            .order_by("at")).all()
    # Hanya aksi yang relevan untuk K3. Login, akun, dan pertanyaan agent yang ditolak tidak ikut,
    # karena laporan ini bisa dibuka viewer.
    audit = db.exec(select(AuditLog).where(AuditLog.at >= start, AuditLog.at < end,
                                           col(AuditLog.action).in_(REPORT_ACTIONS))
                    .order_by(col(AuditLog.at).desc()).limit(MAX_AUDIT)).all()
    cfg = load_config()
    zones = cfg.get("zones", [])
    now = Q.now_local()
    e = escape

    rows_apd = "".join(
        f"<tr><td>{e(x['label'])}</td><td class=n>{_num(x['persen'], '%')}</td><td class=n>{x['patuh']}/{x['total']}</td>"
        f"<td class=n>{m['menit']}</td></tr>"
        for x, m in zip(s["kepatuhan_per_apd"], s["menit_paparan_per_apd"]))
    rows_shift = "".join(
        f"<tr><td>{e(x['shift'])}</td><td class=n>{x['pekerja']}</td><td class=n>{_num(x['compliance_rate'], '%')}</td>"
        f"<td class=n>{x['pelanggaran']}</td><td class=n>{x['menit_paparan']}</td></tr>" for x in s["per_shift"])
    rows_zone = "".join(f"<tr><td>{e(x['zona'])}</td><td class=n>{x['jumlah']}</td></tr>" for x in s["per_zona"])
    rows_find = "".join(
        f"<tr><td>{i}</td><td>{_fmt(v.started_at)}</td><td>{e(v.camera_id)}<br><span class=m>{e(v.zone)}</span></td>"
        f"<td>{e(Q.worker_label(v))}</td><td>{e(', '.join(Q.ITEM_LABEL.get(x, x) for x in v.missing_items.split(',') if x))}</td>"
        f"<td class=n>{_num(round(v.duration_s) if v.duration_s else None, ' dtk')}</td>"
        f"<td>{e({'confirmed': 'Terkonfirmasi', 'pending': 'Belum ditinjau'}.get(v.review, v.review))}</td>"
        f"<td>{_img(v.evidence_crop)}</td>"
        f"<td class=h>{e(v.evidence_sha256[:16])}…</td></tr>"
        for i, v in enumerate(findings, 1))
    rows_int = "".join(f"<tr><td>{_fmt(i.at)}</td><td>{e(i.title)}</td><td>{e(i.camera_id or 'Semua kamera')}</td>"
                       f"<td>{e(i.note)}</td></tr>" for i in interventions) or \
        "<tr><td colspan=4 class=m>Tidak ada intervensi tercatat di periode ini.</td></tr>"
    rows_audit = "".join(f"<tr><td>{_fmt(a.at)}</td><td>{e(a.action)}</td><td>{a.violation_id or '-'}</td>"
                         f"<td>{e(a.detail)}</td></tr>" for a in audit) or \
        "<tr><td colspan=4 class=m>Tidak ada aktivitas tercatat.</td></tr>"
    rows_rules = "".join(
        f"<tr><td>{e(str(z['name']))}</td><td>{e(', '.join(str(Q.ITEM_LABEL.get(str(i), str(i))) for i in (z.get('required') or [])))}</td></tr>"
        for z in zones)
    jam = ", ".join(f"{h:02d}:00" for h in s["jam_rawan"]) or "-"
    more = f"<p class=m>Menampilkan {MAX_FINDINGS} dari {total_findings} temuan.</p>" if total_findings > MAX_FINDINGS else ""

    return f'''<!doctype html><html lang=id><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Laporan Pemantauan APD {_fmt(start)} – {_fmt(end)}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#fff;color:#1d2330;font:15px/1.5 "Archivo",system-ui,-apple-system,sans-serif;font-feature-settings:"tnum" 1}}
.page{{min-height:0;background:#fff}}
.paper{{max-width:794px;margin:0 auto;background:#fff;color:#1d2330;padding:24px 32px;display:flex;flex-direction:column;gap:18px;font-size:12px}}
.paper h1{{font-size:28px;color:#191D1A;margin:0}}
.paper h2{{margin:0;font:700 19px/1.2 'Big Shoulders Display',sans-serif;color:#191D1A;border-bottom:1px solid #d9dde4;padding-bottom:4px}}
.tbl{{width:100%;border-collapse:collapse;margin:6px 0;font-size:12px}}
.tbl th, .tbl td{{border:0;border-bottom:1px solid #e5e8ee;padding:6px 8px;text-align:left;vertical-align:top}}
.tbl th{{color:#4a5262;border-bottom-color:#c9ced8;font-weight:600}}
.num, .n{{text-align:right}}
.k{{border:1px solid #d9dde4;border-radius:8px;padding:10px 12px}}
.kv{{font:800 28px/1.1 'Big Shoulders Display',sans-serif;font-variant-numeric:tabular-nums;margin:4px 0}}
.kl{{font-size:12px;color:#4a5262;font-weight:500;text-transform:uppercase}}
.muted, .m{{color:#4a5262}}
.red{{color:#b42318}} .green{{color:#16733a}}
.pth{{background:#eef0f4;border-radius:4px;height:48px;width:48px;display:flex;align-items:flex-end;justify-content:center;position:relative;overflow:hidden}}
.pth img{{width:100%;height:100%;object-fit:cover}}
.row{{display:flex;align-items:center}}
.between{{justify-content:space-between}}
.note{{background:#f7f7f7;border-left:3px solid #F2B705;padding:8px 10px}}
.toolbar{{margin:12px auto;max-width:794px}} @media print{{.toolbar{{display:none}} .paper{{padding:0}} h2{{break-after:avoid}} tr{{break-inside:avoid}}}}
</style></head><body>
<div class="toolbar"><button onclick="window.print()">Cetak / Simpan PDF</button></div>
<div class="page"><div class="paper">
<div class="row between">
<div class="row" style="gap: 10px"><svg width="30" height="30" viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="9" fill="#F28A2E"></rect><path d="M8.5 25.5a11.5 11.5 0 0 1 23 0z" fill="#1d2330"></path><rect x="18.25" y="13.2" width="3.5" height="9" rx="1.2" fill="#F28A2E"></rect><rect x="6" y="26.5" width="28" height="4" rx="2" fill="#1d2330"></rect></svg>
<div><h1>Laporan Kepatuhan APD</h1><div class="muted">{_fmt(start)} – {_fmt(end)}</div></div></div>
<div class="muted" style="text-align: right">Dibuat {_fmt(now)} WIB<br>Sumber: APD Vision</div>
</div>

<div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px">
<div class="k"><div class="kl">Compliance rate</div><div class="kv green">{_num(s['compliance_rate'], '%')}</div><div class="muted">{s['pekerja_patuh']} dari {s['pekerja_terpantau']} pekerja</div></div>
<div class="k"><div class="kl">Paparan tanpa APD</div><div class="kv red">{s['menit_paparan']} mnt</div><div class="muted">orang-menit</div></div>
<div class="k"><div class="kl">Event pelanggaran</div><div class="kv">{s['event_pelanggaran']}</div><div class="muted">{s['pekerja_melanggar']} pekerja</div></div>
<div class="k"><div class="kl">Menunggu review</div><div class="kv">{s['menunggu_review']}</div></div>
</div>

<h2>Kepatuhan dan paparan per jenis APD</h2>
<table class="tbl"><thead><tr><th>APD</th><th class="num">Kepatuhan</th><th class="num">Patuh / terpantau</th><th class="num">Paparan (mnt)</th></tr></thead><tbody>{rows_apd}</tbody></table>

<div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px">
<div>
<h2>Kinerja per shift</h2>
<table class="tbl"><thead><tr><th>Shift</th><th class="num">Pekerja</th><th class="num">Kepatuhan</th><th class="num">Pelanggaran</th><th class="num">Paparan</th></tr></thead><tbody>{rows_shift}</tbody></table>
</div>
<div>
<h2>Kinerja per zona</h2>
<table class="tbl"><thead><tr><th>Zona</th><th class="num">Pelanggaran</th></tr></thead><tbody>{rows_zone or '<tr><td colspan=2 class=m>-</td></tr>'}</tbody></table>
</div>
</div>

<div><h2>Jam rawan</h2><p style="margin-top: 6px">Pelanggaran terbanyak pukul <b>{jam}</b>.</p></div>

<h2>Tindak lanjut</h2>
<p>Hasil peninjauan temuan oleh supervisor: {reviewed.get('confirmed', 0)} terkonfirmasi, {reviewed.get('false_positive', 0)} false positive, {reviewed.get('pending', 0)} belum ditinjau.</p>
<table class="tbl"><thead><tr><th>Tanggal</th><th>Intervensi</th><th>Kamera</th><th>Catatan</th></tr></thead><tbody>{rows_int}</tbody></table>

<h2>Aturan APD yang diterapkan</h2>
<table class="tbl"><thead><tr><th>Zona</th><th>APD wajib</th></tr></thead><tbody>{rows_rules}</tbody></table>

<h2>Jejak audit K3</h2>
<table class="tbl"><thead><tr><th>Waktu</th><th>Aksi</th><th>ID temuan</th><th>Detail</th></tr></thead><tbody>{rows_audit}</tbody></table>

<h2>Daftar temuan dan bukti</h2>
<p class="muted small">Temuan yang ditandai false positive oleh supervisor tidak dicantumkan. Hash SHA-256 memastikan foto bukti tidak diubah setelah direkam; hash lengkap tersedia di sistem.</p>
<table class="tbl"><thead><tr><th>#</th><th>Waktu</th><th>Kamera / zona</th><th>Pekerja</th><th>APD kurang</th><th class="num">Durasi</th><th>Status</th><th>Bukti</th><th>Hash</th></tr></thead>
<tbody>{rows_find or '<tr><td colspan=9 class=m>Tidak ada temuan di periode ini.</td></tr>'}</tbody></table>{more}

<h2>Metodologi dan keterbatasan</h2>
<div class="note">
<p>Deteksi dilakukan otomatis dari CCTV. Pelanggaran baru dicatat setelah status stabil beberapa frame. Bagian tubuh yang tidak terlihat dicatat sebagai "tidak pasti", bukan pelanggaran.</p>
<p>"Pekerja" adalah ID pelacakan anonim yang dipertahankan selama re-entry tracker cocok; label ini bukan nama pekerja atau identitas biometrik. Wajah pada bukti disamarkan.</p>
<p><b>Dokumen ini adalah bukti pendukung pemantauan, bukan laporan audit SMK3 atau ISO 45001 resmi.</b></p>
</div>
</div></div>
</body></html>'''