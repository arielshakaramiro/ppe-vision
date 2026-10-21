import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { fmtTime, ITEM_LABEL, type Violation } from "../../../api";
import { PpeBoxes, WorkerBox, WorkerCard, type PpeItem } from "../../../ui";
import { SafeVideo } from "../../recordings/components/SafeVideo";
import "../evidence.css";

const ITEM_CODE_BY_LABEL = Object.fromEntries(Object.entries(ITEM_LABEL).map(([code, label]) => [label.toLowerCase(), code]));

function evidenceItemCodes(codes: string[] | undefined, labels: string[] | undefined) {
  const validCodes = new Set(Object.keys(ITEM_LABEL));
  const fromCodes = (codes ?? []).filter((code) => validCodes.has(code));
  if (fromCodes.length) return fromCodes;
  return (labels ?? []).map((label) => ITEM_CODE_BY_LABEL[label.toLowerCase()]).filter(Boolean);
}

export function EvidenceModal({ v, onClose, onReview }: { v: Violation | null; onClose: () => void; onReview?: (v: Violation, r: string, correctedItems?: string[]) => Promise<void> | void }) {
  const [showHash, setShowHash] = useState(false);
  const [showPhoto, setShowPhoto] = useState(false);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<number | null>(null);
  const [correctedItems, setCorrectedItems] = useState<string[]>([]);
  const [reviewStatus, setReviewStatus] = useState<{ tone: "saving" | "success" | "error"; text: string } | null>(null);
  const [savingReview, setSavingReview] = useState(false);
  const closeBtn = useRef<HTMLButtonElement>(null);
  const showPhotoRef = useRef(false);
  // onClose biasanya fungsi inline yang berubah tiap render (halaman Live render ulang tiap 2 detik);
  // simpan di ref supaya efek di bawah tidak berjalan ulang dan memindahkan fokus.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  showPhotoRef.current = showPhoto;
  const isOpen = v !== null;

  useEffect(() => {
    if (!isOpen) return;
    const prev = document.activeElement as HTMLElement | null;
    closeBtn.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (showPhotoRef.current) return setShowPhoto(false);
        return onCloseRef.current();
      }
      if (e.key !== "Tab") return;
      // Jebak fokus di dalam modal supaya pengguna keyboard tidak tersesat ke halaman di belakangnya.
      const modal = closeBtn.current?.closest(".modal");
      const items = modal ? Array.from(modal.querySelectorAll<HTMLElement>("button, a[href], video[controls], input, select")) : [];
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus(); // kembalikan fokus ke elemen yang membuka modal
    };
  }, [isOpen]);

  useEffect(() => {
    setShowHash(false);
    setShowPhoto(false);
    setSelectedEvidenceId(null);
    setReviewStatus(null);
    setSavingReview(false);
  }, [v?.id]);

  useEffect(() => {
    const items = v?.event_bukti?.length ? v.event_bukti : (v ? [v] : []);
    const active = items.find((item) => item.id === selectedEvidenceId) ?? items[0];
    const missingCodes = evidenceItemCodes(active?.apd_kurang_kode, active?.apd_kurang);
    const uncertainCodes = evidenceItemCodes(active?.apd_tidak_pasti_kode, active?.apd_tidak_pasti);
    setCorrectedItems(active?.apd_dibenarkan_kode?.length ? active.apd_dibenarkan_kode : [...missingCodes, ...uncertainCodes]);
  }, [v?.id, selectedEvidenceId]);

  if (!v) return null;
  const evidenceItems = v.event_bukti?.length ? v.event_bukti : [v];
  const activeEvidence = evidenceItems.find((item) => item.id === selectedEvidenceId) ?? evidenceItems[0] ?? v;
  const isWorkerGroup = evidenceItems.length > 1;
  const missingCodes = evidenceItemCodes(activeEvidence.apd_kurang_kode, activeEvidence.apd_kurang);
  const uncertainCodes = evidenceItemCodes(activeEvidence.apd_tidak_pasti_kode, activeEvidence.apd_tidak_pasti);
  const correctionOptions = activeEvidence.jenis === "violation" ? missingCodes : uncertainCodes;
  const selectedCorrectionLabels = correctedItems.filter((item) => correctionOptions.includes(item)).map((item) => ITEM_LABEL[item] ?? item);
  const activeLabel = activeEvidence.jenis === "violation" ? `Tanpa ${activeEvidence.apd_kurang.join(", ")}` : `Tidak pasti: ${activeEvidence.apd_tidak_pasti.join(", ")}`;
  const groupLabel = v.jenis === "violation" ? `Tanpa ${v.apd_kurang.join(", ")}` : `Tidak pasti: ${v.apd_tidak_pasti.join(", ")}`;
  const evidenceMetaLabel = isWorkerGroup ? `${activeEvidence.pekerja} · Kartu pekerja · ${evidenceItems.length} bukti` : activeEvidence.pekerja;
  const detection = activeEvidence.deteksi;
  const photoItems: PpeItem[] = detection?.items.map((item) => ({
    key: item.key,
    status: item.status === "not_required" ? "unchecked" : item.status,
    boxes: item.boxes.map(({ box }) => box),
    confidence: item.status === "present" && item.boxes.length
      ? item.boxes.reduce((sum, entry) => sum + entry.confidence, 0) / item.boxes.length : undefined,
  })) ?? [];
  const toggleCorrected = (item: string) => {
    setCorrectedItems((items) => items.includes(item) ? items.filter((x) => x !== item) : [...items, item]);
  };
  const submitReview = async (review: string, corrected: string[] = []) => {
    if (!onReview || savingReview) return;
    setSavingReview(true);
    setReviewStatus({ tone: "saving", text: "Menyimpan review…" });
    try {
      await onReview(activeEvidence, review, corrected);
      const label = review === "false_positive" ? "bukan pelanggaran" : "benar pelanggaran";
      setReviewStatus({ tone: "success", text: `Review berhasil disimpan sebagai ${label}.` });
    } catch (e) {
      setReviewStatus({ tone: "error", text: `Review gagal disimpan: ${(e as Error).message}` });
    } finally {
      setSavingReview(false);
    }
  };
  const submitFalsePositive = () => submitReview("false_positive", correctedItems.filter((item) => correctionOptions.includes(item)));
  const reviewLabel = activeEvidence.review === "pending" ? "Menunggu review" : activeEvidence.review === "confirmed" ? "Terkonfirmasi" : "Positif palsu";

  return createPortal(
    <div className="modal-bg evidence-modal-bg" onClick={onClose}>
      <div className="modal evidence-modal" role="dialog" aria-modal="true" aria-label={`Detail evidence ${v.pekerja}`} onClick={(e) => e.stopPropagation()}>
        <button ref={closeBtn} className="modal-close" onClick={onClose} aria-label="Tutup modal">×</button>
        <div className="evidence-modal-media">
          {activeEvidence.klip ? <SafeVideo src={activeEvidence.klip} poster={activeEvidence.foto || activeEvidence.foto_crop || undefined} className="modal-video" label="Klip evidence" /> : (activeEvidence.foto ? <img src={activeEvidence.foto} alt="Foto evidence" className="modal-img" /> : <div className="modal-img thumb-empty">Tanpa foto</div>)}
        </div>
        <div className="modal-meta">
          <div className="evidence-detail-main">
            <span className="evidence-detail-kicker">{activeEvidence.jenis === "violation" ? "Pelanggaran" : "Tidak pasti"}{activeEvidence.sumber ? ` · ${activeEvidence.sumber}` : ""}</span>
            <h2 className={v.jenis === "violation" ? "bad" : "warn"}>{isWorkerGroup ? groupLabel.replace(/^Tanpa /, "") : activeLabel.replace(/^Tanpa /, "")}</h2>
            <div className="muted small">
              {evidenceMetaLabel} · {activeEvidence.kamera} · {activeEvidence.zona || "Tanpa zona"} · {fmtTime(activeEvidence.waktu)}
              {activeEvidence.durasi_detik != null && ` · ${Math.round(activeEvidence.durasi_detik)} detik`}
              {activeEvidence.versi_model && ` · model ${activeEvidence.versi_model}`}
            </div>
            {isWorkerGroup && (
              <div className="worker-evidence-list" aria-label="Daftar waktu dan foto bukti pekerja">
                <div className="worker-evidence-list-head">
                  <b>{evidenceItems.length} bukti</b>
                  <span className="muted small">Pilih satu bukti untuk melihat detail dan melakukan review event tersebut.</span>
                </div>
                <div className="worker-evidence-items">
                  {evidenceItems.map((item, idx) => (
                    <button key={item.id} type="button" className={item.id === activeEvidence.id ? "on" : ""} onClick={() => setSelectedEvidenceId(item.id)}>
                      <span className="evidence-sequence">#{idx + 1}</span>
                      <span className="evidence-event-copy">
                        <b>{fmtTime(item.waktu)}</b>
                        <small>{item.jenis === "violation" ? item.apd_kurang.join(", ") : `Tidak pasti: ${item.apd_tidak_pasti.join(", ")}`}</small>
                      </span>
                      <small>{item.durasi_detik != null ? `${Math.round(item.durasi_detik)} dtk` : item.pekerja}</small>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="evidence-actions" aria-label="Aksi evidence">
              {activeEvidence.foto && (
                <button type="button" className="av-btn ghost sm" onClick={() => setShowPhoto(true)}>
                  Buka foto penuh
                </button>
              )}
              <button type="button" className="av-btn ghost sm" onClick={() => setShowHash(!showHash)}>
                {showHash ? "Sembunyikan hash" : "Lihat hash SHA-256"}
              </button>
            </div>
            {showHash && (
              <div className="hash-panel" role="status" aria-label="Hash SHA-256 evidence">
                <div>
                  <span>Foto</span>
                  <code>{activeEvidence.sha256 || "Tidak ada hash foto"}</code>
                </div>
                {activeEvidence.klip_sha256 && (
                  <div>
                    <span>Klip</span>
                    <code>{activeEvidence.klip_sha256}</code>
                  </div>
                )}
              </div>
            )}
          </div>
          {onReview && (
            <div className="review-panel">
              <div className="evidence-review-status">
                <span className="muted small">Review:</span>
                <span className={`pill ${activeEvidence.review}`}>{reviewLabel}</span>
              </div>
              <div className="evidence-review-actions">
                <button className="av-btn evidence-confirm" disabled={savingReview} onClick={() => submitReview("confirmed")}>Benar pelanggaran</button>
                <button className="av-btn" disabled={savingReview} onClick={submitFalsePositive}>Bukan pelanggaran</button>
              </div>
              {reviewStatus && <p className={`review-feedback ${reviewStatus.tone}`} role="status">{reviewStatus.text}</p>}
              {correctionOptions.length > 0 && (
                <div className="correction-box">
                  <div>
                    <b>Koreksi untuk positif palsu</b>
                    <p className="muted small">Centang APD yang sebenarnya dipakai oleh pekerja. Pilihan ini dikirim saat tombol “Bukan pelanggaran” ditekan.</p>
                  </div>
                  <div className="correction-options">
                    {correctionOptions.map((item) => (
                      <label key={item} className="check-option">
                        <input type="checkbox" checked={correctedItems.includes(item)} onChange={() => toggleCorrected(item)} />
                        <span>{ITEM_LABEL[item] ?? item}</span>
                      </label>
                    ))}
                  </div>
                  <p className="correction-summary small">
                    Dikirim sebagai koreksi: <b>{selectedCorrectionLabels.length ? selectedCorrectionLabels.join(", ") : "Tidak ada APD yang dipilih"}</b>.
                  </p>
                  {(activeEvidence.apd_dibenarkan?.length ?? 0) > 0 && (
                    <p className="muted small">Tersimpan sebagai koreksi: {activeEvidence.apd_dibenarkan?.join(", ")}.</p>
                  )}
                </div>
              )}
              {correctionOptions.length === 0 && (
                <p className="note correction-note">APD yang dilaporkan tidak tersedia untuk dikoreksi pada evidence ini. Sistem tetap dapat menandai event sebagai bukan pelanggaran, tetapi tidak ada APD spesifik yang dikirim untuk retraining.</p>
              )}
            </div>
          )}
        </div>
        {showPhoto && activeEvidence.foto && (
          <div className="photo-viewer-bg" role="dialog" aria-modal="true" aria-label="Pratinjau foto evidence" onClick={() => setShowPhoto(false)}>
            <div className="photo-viewer" onClick={(e) => e.stopPropagation()}>
              <div className="photo-viewer-head">
                <div>
                  <b>Foto evidence</b>
                  <p className="muted small">{activeEvidence.kamera} · {fmtTime(activeEvidence.waktu)}</p>
                </div>
                <button type="button" className="modal-close inline" onClick={() => setShowPhoto(false)} aria-label="Tutup foto">×</button>
              </div>
              {activeEvidence.foto_clean && detection?.worker?.box ? (
                <div className="photo-detection-layout">
                  <div className="photo-detection-stage">
                    <img src={activeEvidence.foto_clean} alt="Foto evidence tanpa anotasi, dengan kotak deteksi" />
                    <div className="photo-detection-overlay">
                      <WorkerBox box={detection.worker.box} tone={activeEvidence.jenis === "violation" ? "bad" : "warn"}
                        label={activeEvidence.pekerja} ariaLabel={`Kotak pekerja ${activeEvidence.pekerja}`} />
                      <PpeBoxes person={detection.worker.box} items={photoItems} detectedOnly />
                    </div>
                  </div>
                  <div className="photo-detection-card">
                    <WorkerCard id={activeEvidence.pekerja} person={detection.worker.box}
                      kind={activeEvidence.jenis === "violation" ? "violation" : "uncertain"}
                      subtitle={`${activeEvidence.kamera} · ${activeEvidence.zona || "Tanpa zona"}`}
                      items={photoItems} />
                  </div>
                </div>
              ) : <img src={activeEvidence.foto} alt="Foto evidence penuh" />}
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
