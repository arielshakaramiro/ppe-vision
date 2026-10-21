import { useEffect, useMemo, useState } from "react";
import { api, can, fmtTime, ITEM_LABEL, PERIODS, type CameraRow, type Violation } from "../../api";
import { useMe } from "../../session";
import { PeriodPicker } from "../../components";
import { EvidenceModal } from "./components/EvidenceModal";
import { GlassSelect, type GlassOption } from "../../ui";
import "./evidence.css";

const REVIEW_LABEL: Record<string, string> = {
  pending: "Menunggu review",
  confirmed: "Terkonfirmasi",
  false_positive: "Positif palsu",
};

const KIND_LABEL: Record<string, string> = {
  violation: "Pelanggaran",
  uncertain: "Tidak pasti",
};

export default function Evidence() {
  const me = useMe();
  const [period, setPeriod] = useState("7_hari");
  const [cameraId, setCameraId] = useState("");
  const [item, setItem] = useState("");
  const [kind, setKind] = useState("violation");
  const [review, setReview] = useState("");
  const [rows, setRows] = useState<Violation[]>([]);
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [cameraNames, setCameraNames] = useState<Record<string, string>>({});
  const [total, setTotal] = useState(0);
  const [totalEvent, setTotalEvent] = useState(0);
  const [open, setOpen] = useState<Violation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.cameras()
      .then((cs) => {
        const sorted = [...cs].sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id, "id-ID"));
        setCameras(sorted);
        setCameraNames(Object.fromEntries(sorted.map((c) => [c.id, c.name || c.id])));
      })
      .catch(() => { setCameras([]); setCameraNames({}); });
  }, []);

  useEffect(() => {
    let active = true; // filter diganti cepat: respons lama tidak boleh menimpa yang baru
    const p: Record<string, string> = { period, kind, limit: "60", source: "semua", group_by_worker: "true" };
    if (cameraId) p.camera_id = cameraId;
    if (item) p.item = item;
    if (review) p.review = review;
    setLoading(true);
    setError(null);
    api.violations(p)
      .then((r) => { if (active) { setRows(r.items); setTotal(r.total); setTotalEvent(r.total_event ?? r.total); } })
      .catch((e: Error) => { if (active) { setRows([]); setTotal(0); setTotalEvent(0); setError(e.message); } })
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [period, cameraId, item, kind, review]);

  const doReview = async (v: Violation, r: string, correctedItems: string[] = []) => {
    try {
      const updated = await api.review(v.id, r, correctedItems);
      setRows((rs) => rs.map((x) => {
        const event_bukti = x.event_bukti?.map((evidence) => (evidence.id === updated.id ? updated : evidence));
        if (x.id === updated.id && x.jumlah_event && x.jumlah_event > 1) return { ...x, review: updated.review, event_bukti };
        if (x.id === updated.id) return { ...x, ...updated, event_bukti };
        return event_bukti ? { ...x, event_bukti } : x;
      }));
      setOpen((current) => {
        if (!current) return updated;
        if (!current.event_bukti?.length) return current.id === updated.id ? { ...current, ...updated } : current;
        const event_bukti = current.event_bukti.map((evidence) => (evidence.id === updated.id ? updated : evidence));
        return {
          ...current,
          ...(current.id === updated.id ? { review: updated.review } : {}),
          event_bukti,
        };
      });
    } catch (e) {
      setError(`Review gagal disimpan: ${(e as Error).message}`);
      throw e;
    }
  };

  const cameraLabel = (id: string) => {
    const name = cameraNames[id];
    if (!name) return id || "Kamera tanpa ID";
    return name === id ? id : `${name} (${id})`;
  };

  const cameraOptionLabel = (c: CameraRow) => (!c.name || c.name === c.id ? c.id : `${c.name} — ${c.id}`);

  // Label sumber per grup, dari field `sumber` evidence (API). Fallback ke jenis kamera bila kosong.
  const SOURCE_LABEL: Record<string, string> = { live: "Live", rekaman: "Rekaman", simulasi: "Simulasi" };
  const groupSourceLabel = (items: Violation[], cameraId: string): string | null => {
    const sources = new Set(items.map((v) => v.sumber).filter(Boolean) as string[]);
    if (sources.size === 1) return SOURCE_LABEL[[...sources][0]] ?? null;
    if (sources.size > 1) return null; // campur: jangan beri label menyesatkan
    // tidak ada data sumber di evidence: tebak dari jenis kamera
    const c = cameras.find((x) => x.id === cameraId);
    if (!c) return null;
    if (c.source_kind !== "video") return "Live";
    if (c.mode === "analisis") return "Rekaman";
    if (c.mode === "simulasi") return "Simulasi";
    return null;
  };

  const cameraGroups = useMemo(() => [
    {
      label: "Kamera live/stream",
      items: cameras.filter((c) => c.source_kind !== "video"),
    },
    {
      label: "Kamera simulasi live",
      items: cameras.filter((c) => c.source_kind === "video" && c.mode === "simulasi"),
    },
    {
      label: "Kamera analisis rekaman",
      items: cameras.filter((c) => c.source_kind === "video" && c.mode === "analisis"),
    },
    {
      label: "Kamera video lainnya",
      items: cameras.filter((c) => c.source_kind === "video" && c.mode !== "simulasi" && c.mode !== "analisis"),
    },
  ].filter((group) => group.items.length > 0), [cameras]);

  const groupedRows = useMemo(() => {
    const groups = new Map<string, Violation[]>();
    rows.forEach((v) => {
      const key = v.kamera || "";
      groups.set(key, [...(groups.get(key) ?? []), v]);
    });
    return [...groups.entries()]
      .map(([cameraId, items]) => ({ cameraId, label: cameraLabel(cameraId), items }))
      .sort((a, b) => a.label.localeCompare(b.label, "id-ID"));
  }, [rows, cameraNames]);

  const eventCount = (v: Violation) => v.jumlah_event ?? v.event_bukti?.length ?? 1;
  const rawEventCount = (v: Violation) => v.event_bukti?.length ?? eventCount(v);
  const groupTimeLabel = (v: Violation) => {
    if (!v.waktu_mulai || !v.waktu_selesai || v.waktu_mulai === v.waktu_selesai) return fmtTime(v.waktu);
    return `${fmtTime(v.waktu_mulai)} – ${fmtTime(v.waktu_selesai)}`;
  };
  const groupSessionLabel = (items: Violation[], camera: string) => {
    const source = groupSourceLabel(items, camera);
    const first = [...items].sort((a, b) => (a.waktu_mulai ?? a.waktu).localeCompare(b.waktu_mulai ?? b.waktu))[0];
    if (!first) return "Sesi kamera";
    const prefix = source === "Rekaman" ? "Analisis rekaman" : source === "Simulasi" ? "Sesi simulasi" : "Sesi live";
    return `${prefix} · mulai ${fmtTime(first.waktu_mulai ?? first.waktu)}`;
  };

  const selectedItemLabel = item ? `Tanpa ${ITEM_LABEL[item]?.toLowerCase() ?? item}` : "Semua APD";
  const selectedReviewLabel = review ? REVIEW_LABEL[review] ?? review : "Semua review";
  const hasCustomFilter = period !== "7_hari" || cameraId !== "" || item !== "" || kind !== "violation" || review !== "";
  const resetFilters = () => {
    setPeriod("7_hari");
    setCameraId("");
    setItem("");
    setKind("violation");
    setReview("");
  };

  return (
    <div className="stack evidence-page">
      <section className="evidence-hero">
        <div className="evidence-title">
          <p className="muted small">Galeri evidence</p>
          <h1>Evidence pelanggaran APD</h1>
        </div>
        <div className="evidence-summary" aria-label="Ringkasan evidence">
          <div>
            <span>Pekerja tampil</span>
            <b>{loading ? "…" : total}</b>
          </div>
          <div>
            <span>Total event</span>
            <b>{loading ? "…" : totalEvent}</b>
          </div>
          <div>
            <span>Kamera tampil</span>
            <b>{groupedRows.length}</b>
          </div>
        </div>
      </section>

      <section className="av-card evidence-filter-card" aria-label="Filter evidence">
        <div className="evidence-filter-head">
          <div>
            <h3>Filter evidence</h3>
            <p className="muted small">Filter hanya mempersempit daftar, data evidence tidak berubah.</p>
          </div>
          <button
            type="button"
            className="av-btn sm evidence-reset-btn"
            onClick={resetFilters}
            disabled={!hasCustomFilter}
            aria-label="Atur ulang filter evidence"
          >
            <span aria-hidden="true">↺</span>
            Atur ulang filter
          </button>
        </div>

        <div className="evidence-period-row">
          <span className="muted small">Periode</span>
          <PeriodPicker value={period} onChange={setPeriod} options={PERIODS} />
        </div>

        <div className="evidence-filter-grid">
          <label className="av-field evidence-field evidence-field-wide">
            <span>Kamera</span>
            <GlassSelect
              className="evidence-select"
              ariaLabel="Sortir kamera"
              value={cameraId}
              onChange={setCameraId}
              options={[
                { value: "", label: "Semua kamera" },
                ...cameraGroups.flatMap((group): GlassOption[] =>
                  group.items.map((c) => ({ value: c.id, label: cameraOptionLabel(c), group: `${group.label} (${group.items.length})` }))),
              ]}
            />
          </label>

          <label className="av-field evidence-field">
            <span>Jenis evidence</span>
            <GlassSelect
              className="evidence-select"
              ariaLabel="Jenis evidence"
              value={kind}
              onChange={setKind}
              options={[{ value: "violation", label: "Pelanggaran" }, { value: "uncertain", label: "Tidak pasti" }]}
            />
          </label>

          <label className="av-field evidence-field">
            <span>APD</span>
            <GlassSelect
              className="evidence-select"
              ariaLabel="APD"
              value={item}
              onChange={setItem}
              options={[{ value: "", label: "Semua APD" }, ...Object.entries(ITEM_LABEL).map(([k, l]) => ({ value: k, label: `Tanpa ${l.toLowerCase()}` }))]}
            />
          </label>

          <label className="av-field evidence-field">
            <span>Status review</span>
            <GlassSelect
              className="evidence-select"
              ariaLabel="Status review"
              value={review}
              onChange={setReview}
              options={[
                { value: "", label: "Semua review" },
                { value: "pending", label: "Menunggu review" },
                { value: "confirmed", label: "Terkonfirmasi" },
                { value: "false_positive", label: "Positif palsu" },
              ]}
            />
          </label>
        </div>

        <div className="evidence-active-filters" aria-label="Filter aktif">
          {cameraId && <button type="button" onClick={() => setCameraId("")} aria-label="Hapus filter kamera">{cameraLabel(cameraId)} <b aria-hidden="true">×</b></button>}
          {kind === "violation" ? <span>{KIND_LABEL[kind]}</span> : <button type="button" onClick={() => setKind("violation")} aria-label="Hapus filter jenis evidence">{KIND_LABEL[kind] ?? kind} <b aria-hidden="true">×</b></button>}
          {item && <button type="button" onClick={() => setItem("")} aria-label="Hapus filter APD">{selectedItemLabel} <b aria-hidden="true">×</b></button>}
          {review && <button type="button" onClick={() => setReview("")} aria-label="Hapus filter status review">{selectedReviewLabel} <b aria-hidden="true">×</b></button>}
          {period === "7_hari" ? <span>Periode: 7 hari</span> : <button type="button" onClick={() => setPeriod("7_hari")} aria-label="Hapus filter periode">Periode: {PERIODS.find(([value]) => value === period)?.[1] ?? period} <b aria-hidden="true">×</b></button>}
          <span>Dikelompokkan per pekerja</span>
        </div>
        <p className="evidence-tracker-note muted small">ID pekerja (W…) berasal dari tracker dan hanya berlaku dalam satu sesi kamera, jadi kartu tidak digabung lintas sesi atau lintas hari.</p>
      </section>

      {error && <p className="av-card error evidence-state">{error}</p>}

      <div className="evidence-groups">
        {groupedRows.map((group) => (
          <section key={group.cameraId || "tanpa-kamera"} className="evidence-group">
            <div className="evidence-group-head">
              <div>
                <h3>
                  <strong>{cameraNames[group.cameraId] || group.cameraId || "Kamera tanpa ID"}</strong>
                  {cameraNames[group.cameraId] && cameraNames[group.cameraId] !== group.cameraId && <span className="evidence-camera-id"> · {group.cameraId}</span>}
                  {groupSourceLabel(group.items, group.cameraId) && <span className="av-tag">{groupSourceLabel(group.items, group.cameraId)}</span>}
                </h3>
                <p className="muted small">{groupSessionLabel(group.items, group.cameraId)}</p>
              </div>
              <span className="evidence-count">{group.items.length} pekerja · {group.items.reduce((sum, v) => sum + eventCount(v), 0)} event</span>
            </div>
            <div className="gallery">
              {group.items.map((v) => (
                <button
                  type="button"
                  key={v.id}
                  className="tile evidence-tile"
                  aria-label={`Buka evidence ${v.pekerja}`}
                  onClick={() => setOpen(v)}
                >
                  <div className="evidence-thumb">
                    {v.foto_crop ? (
                      <>
                        <img src={v.foto_crop} alt="" />
                        <span className="evidence-privacy-label">Wajah disamarkan</span>
                      </>
                    ) : (
                      <div className="tile-thumb-empty">
                        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="3" /><circle cx="12" cy="12" r="3.2" /></svg>
                        <span>Tanpa foto</span>
                      </div>
                    )}
                    <span className={`evidence-kind ${v.jenis}`}>{KIND_LABEL[v.jenis] ?? v.jenis}</span>
                    <span className="evidence-event-badge">{rawEventCount(v)} bukti</span>
                  </div>
                  <div className="tile-meta">
                    <div className={`evidence-tile-title ${v.jenis === "violation" ? "bad" : "warn"}`}>
                      {v.jenis === "violation" ? v.apd_kurang.join(", ") : `? ${v.apd_tidak_pasti.join(", ")}`}
                    </div>
                    <div className="muted small">{v.pekerja} · {v.zona || "Tanpa zona"}</div>
                    <div className="muted small">{groupTimeLabel(v)}</div>
                    {v.indikasi_fragmentasi_tracking && <div className="muted small">Klaster visual konservatif</div>}
                    <span className={`pill ${v.review}`}>{REVIEW_LABEL[v.review] ?? v.review.replace("_", " ")}</span>
                  </div>
                </button>
              ))}
            </div>
          </section>
        ))}
        {loading && !rows.length && <div className="av-card evidence-state"><b>Memuat evidence…</b><span className="muted small">Data sedang diambil sesuai filter yang dipilih.</span></div>}
        {!loading && !error && !rows.length && (
          <div className="av-card evidence-state">
            <b>Tidak ada evidence</b>
            <span className="muted small">Tidak ditemukan evidence untuk kombinasi filter saat ini.</span>
            {hasCustomFilter && (
              <button type="button" className="av-btn sm evidence-reset-btn" onClick={resetFilters} aria-label="Atur ulang filter evidence">
                <span aria-hidden="true">↺</span>
                Atur ulang filter
              </button>
            )}
          </div>
        )}
      </div>
      <EvidenceModal v={open} onClose={() => setOpen(null)} onReview={can(me, "supervisor") ? doReview : undefined} />
    </div>
  );
}
