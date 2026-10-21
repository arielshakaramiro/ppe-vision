import { useEffect, useState } from "react";
import { api, fmtTime, PERIODS, type AuditLogItem, type Violation } from "../../api";
import { EvidenceModal, PeriodPicker } from "../../components";
import { GlassSelect } from "../../ui";
import "./admin-pages.css";

const ACTION_LABELS: Record<string, string> = {
  review_confirmed: "Review: benar",
  review_false_positive: "Review: false positive",
  review_pending: "Review: pending",
  zone_required_diubah: "Ubah APD wajib",
  zone_dibuat: "Buat zona",
  zone_diubah: "Ubah zona",
  zone_dihapus: "Hapus zona",
  camera_switch: "Ganti kamera",
  camera_dibuat: "Tambah kamera",
  camera_diubah: "Ubah kamera",
  camera_dihapus: "Hapus kamera",
  video_diupload: "Unggah video",
  video_didaftarkan: "Daftarkan video server",
  job_dibuat: "Buat analisis rekaman",
  job_pause: "Jeda analisis",
  job_resume: "Lanjut analisis",
  job_cancel: "Batal analisis",
  hasil_review_diekspor: "Ekspor hasil review",
  intervention_created: "Catat intervensi",
  intervention_deleted: "Hapus intervensi",
  pengaturan_diubah: "Ubah pengaturan",
  agent_ditolak: "Agent ditolak",
  retensi: "Retensi data",
  login: "Login",
  login_gagal: "Login gagal",
  password_diganti: "Password diganti",
  password_gagal: "Password gagal",
  user_dibuat: "Buat akun",
  user_diubah: "Ubah akun",
};

// Warna hanya untuk makna (SPEC): hijau = hasil review/intervensi positif,
// koral = penolakan/kegagalan, kuning = perubahan konfigurasi. Sisanya netral.
const ACTION_TONE: Record<string, "good" | "bad" | "warn"> = {
  review_confirmed: "good",
  review_false_positive: "good",
  review_pending: "warn",
  intervention_created: "good",
  zone_required_diubah: "warn",
  zone_dibuat: "warn",
  zone_diubah: "warn",
  zone_dihapus: "warn",
  camera_switch: "warn",
  camera_diubah: "warn",
  pengaturan_diubah: "warn",
  agent_ditolak: "bad",
  login_gagal: "bad",
  password_gagal: "bad",
};

export default function Audit() {
  const [period, setPeriod] = useState("7_hari");
  const [actor, setActor] = useState("");
  const [actors, setActors] = useState<string[]>([]);
  const [action, setAction] = useState("");
  const [data, setData] = useState<{ total: number; items: AuditLogItem[] } | null>(null);
  const [open, setOpen] = useState<Violation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.audit({ period, actor, action, limit: "100" })
      .then((rows) => { setData(rows); setError(null); })
      .catch((e: Error) => setError(e.message));
  }, [period, actor, action]);

  useEffect(() => {
    api.auditActors({ period })
      .then((result) => setActors(result.items))
      .catch((e: Error) => setError(e.message));
  }, [period]);

  const openEvidence = async (id: number) => {
    try {
      setOpen(await api.violation(id));
      setError(null);
    } catch (e) {
      setError(`Evidence tidak dapat dibuka: ${(e as Error).message}`);
    }
  };

  const reviewEvidence = async (v: Violation, review: string, correctedItems: string[] = []) => {
    try {
      setOpen(await api.review(v.id, review, correctedItems));
      setError(null);
    } catch (e) {
      setError(`Review gagal disimpan: ${(e as Error).message}`);
      throw e;
    }
  };

  return (
    <div className="audit-page">
      <div className="admin-page-heading">
        <h1>Audit log</h1>
        <p className="muted small">Semua aksi manusia dan sistem. Catatan hanya bisa ditambah; tidak ada yang bisa mengubah atau menghapusnya dari aplikasi.</p>
      </div>
      <div className="audit-toolbar">
        <PeriodPicker value={period} onChange={setPeriod} options={PERIODS} />
        <GlassSelect
          ariaLabel="Filter aktor"
          value={actor}
          onChange={setActor}
          options={[{ value: "", label: "Semua aktor" }, ...actors.map((name) => ({ value: name, label: name }))]}
        />
        <GlassSelect
          ariaLabel="Filter aksi"
          value={action}
          onChange={setAction}
          options={[{ value: "", label: "Semua aksi" }, ...Object.entries(ACTION_LABELS).map(([k, v]) => ({ value: k, label: v }))]}
        />
        {data && <span className="audit-count muted small">{data.total} catatan</span>}
      </div>
      {error && <div className="note error">{error}</div>}
      <section className="av-card audit-card">
        <div className="table-wrap">
          <table className="av-table audit-table" aria-label="Audit log">
            <thead>
                <tr><th>Waktu</th><th>Aktor</th><th>Aksi</th><th>Detail</th><th>Event</th></tr>
            </thead>
            <tbody>
              {data?.items.map((i, idx) => (
                <tr key={idx}>
                  <td className="muted small">{fmtTime(i.waktu)}</td>
                  <td><b>{i.aktor}</b></td>
                  <td className={ACTION_TONE[i.aksi] ?? ""}>{ACTION_LABELS[i.aksi] || i.aksi}</td>
                  <td className="muted">{i.detail}</td>
                  <td>
                    {i.violation_id != null ? (
                      <button type="button" className="link small" onClick={() => openEvidence(i.violation_id!)}>
                        #{i.violation_id}
                      </button>
                    ) : <span className="muted">–</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data?.items.length && <p className="muted small" style={{ padding: 16 }}>Tidak ada data audit.</p>}
        </div>
      </section>
      <EvidenceModal v={open} onClose={() => setOpen(null)} onReview={reviewEvidence} />
    </div>
  );
}
