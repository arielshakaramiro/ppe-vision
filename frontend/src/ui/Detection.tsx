import type { ReactNode } from "react";

/**
 * Overlay deteksi bergaya Live: kotak siku pekerja, kotak per APD, dan kartu pekerja glass.
 * Semua koordinat dalam PERSEN terhadap frame video/foto (0–100), jadi tidak bergantung ukuran layar.
 * Bungkus dengan elemen .av-stage (position:relative, aspect-ratio 16/9).
 *
 * Kalau backend menyimpan koordinat piksel, ubah dulu: x% = x_px / lebar_frame * 100.
 */
export type Box = { x: number; y: number; w: number; h: number };
export type PpeKey = "helmet" | "vest" | "gloves" | "shoes";
export type PpeStatus = "present" | "missing" | "unknown" | "unchecked";
export type PpeItem = { key: PpeKey; status: PpeStatus; confidence?: number; box?: Box; boxes?: Box[] };

export const PPE_LABEL: Record<PpeKey, string> = { helmet: "Helm", vest: "Rompi", gloves: "Sarung tangan", shoes: "Sepatu" };
const pct = (n: number) => `${n}%`;
const fmtConf = (c?: number) => (c == null ? "" : ` · ${c.toFixed(2).replace(".", ",")}`);

/** Kotak siku pekerja. tone: good (patuh), bad (pelanggaran), warn (tidak pasti). */
export function WorkerBox({ box, tone = "good", label, muted, selected, onClick, ariaLabel }: {
  box: Box; tone?: "good" | "bad" | "warn"; label?: ReactNode; muted?: boolean; selected?: boolean; onClick?: () => void; ariaLabel?: string;
}) {
  const style = { left: pct(box.x), top: pct(box.y), width: pct(box.w), height: pct(box.h) };
  const cls = `av-box ${tone === "good" ? "" : tone} ${muted ? "mute" : ""} ${selected ? "sel" : ""}`;
  const inner = (<><i />{label != null && <span className={`av-box-label ${muted ? "mute" : ""}`}>{label}</span>}</>);
  return onClick
    ? <button type="button" className={cls} style={style} onClick={onClick} aria-label={ariaLabel}>{inner}</button>
    : <span className={cls} style={style} aria-hidden={!ariaLabel} aria-label={ariaLabel}>{inner}</span>;
}

/**
 * Perkiraan posisi APD dari kotak pekerja. HANYA dipakai kalau backend belum mengirim
 * kotak per APD (PpeItem.box). Begitu model mengirim kotaknya, kotak asli yang dipakai.
 */
export function estimatePpeBoxes(p: Box): Record<PpeKey, Box[]> {
  const r = (fx: number, fy: number, fw: number, fh: number): Box => ({ x: p.x + fx * p.w, y: p.y + fy * p.h, w: fw * p.w, h: fh * p.h });
  return {
    helmet: [r(0.25, 0, 0.5, 0.16)],
    vest: [r(0.15, 0.2, 0.7, 0.36)],
    gloves: [r(0, 0.5, 0.2, 0.12), r(0.8, 0.5, 0.2, 0.12)],
    shoes: [r(0.15, 0.9, 0.7, 0.1)],
  };
}

const TONE: Record<PpeStatus, string> = { missing: "var(--av-bad)", present: "var(--av-good)", unknown: "var(--av-warn)", unchecked: "var(--av-mute)" };
const MARK: Record<PpeStatus, string> = { missing: "✕", present: "✓", unknown: "?", unchecked: "–" };

/** Kotak per APD di dalam kotak pekerja (khusus foto evidence). */
export function PpeBoxes({ person, items, labelSide, detectedOnly = false }: { person: Box; items: PpeItem[]; labelSide?: "l" | "r"; detectedOnly?: boolean }) {
  const est = detectedOnly ? null : estimatePpeBoxes(person);
  const right = person.x + person.w / 2 > 55;
  const side = labelSide ?? (right ? (person.x + person.w > 82 ? "l" : "r") : (person.x < 12 ? "r" : "l"));
  return (
    <>
      {items.flatMap((it) => {
        const boxes = it.boxes ?? (it.box ? [it.box] : (est?.[it.key] ?? []));
        return boxes.map((b, i) => {
          const showLabel = boxes.length === 1 || (side === "l" ? i === 0 : i === boxes.length - 1);
          const ls = it.key === "helmet" ? "t" : boxes.length > 1 ? (i === 0 ? "l" : "r") : side;
          const boxStatus = detectedOnly ? "present" : it.status;
          return (
            <span key={`${it.key}-${i}`} className={`av-ppe ${boxStatus === "unchecked" ? "na" : ""}`}
              style={{ left: pct(b.x), top: pct(b.y), width: pct(b.w), height: pct(b.h), color: TONE[boxStatus] }}>
              {showLabel && <span className={ls}>{PPE_LABEL[it.key]} {MARK[boxStatus]}</span>}
            </span>
          );
        });
      })}
    </>
  );
}

const HELMET_OFF = (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 16.5h18" /><path d="M5 16.5V14a7 7 0 0 1 14 0v2.5" /><path d="M12 7v4" /><path d="M4 4l16 16" />
  </svg>
);
const HELMET_OK = (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 16.5h18" /><path d="M5 16.5V14a7 7 0 0 1 14 0v2.5" /><path d="M12 7v4" />
  </svg>
);

/**
 * Kartu pekerja glass. Diletakkan di samping kotak pekerja, di sisi yang lebih lapang.
 * kind: "violation" | "uncertain" | "ok".
 */
export function WorkerCard({ id, person, kind, subtitle, items, onClose, action, gap = 120 }: {
  id: string; person: Box; kind: "violation" | "uncertain" | "ok"; subtitle?: ReactNode; items: PpeItem[];
  onClose?: () => void; action?: ReactNode; gap?: number;
}) {
  const right = person.x + person.w / 2 > 55;
  const style = right
    ? { right: `calc(${100 - person.x}% + ${gap}px)`, top: pct(Math.max(3, Math.min(person.y, 48))) }
    : { left: `calc(${person.x + person.w}% + ${gap}px)`, top: pct(Math.max(3, Math.min(person.y, 48))) };
  const head = { violation: ["Pelanggaran", "var(--av-bad)"], uncertain: ["Tidak pasti", "var(--av-warn)"], ok: ["APD lengkap", "var(--av-good)"] }[kind];
  const checked = items.filter((i) => i.status !== "unchecked");
  const skipped = items.filter((i) => i.status === "unchecked").map((i) => PPE_LABEL[i.key].toLowerCase());
  return (
    <section className="av-worker" style={style} aria-label={`Detail pekerja ${id}`}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="av-worker-kind" style={{ color: head[1] }}>{head[0]}</span>
        {onClose && <button type="button" className="av-icon-btn" style={{ width: 30, height: 30, borderRadius: "50%" }} onClick={onClose} aria-label="Tutup kartu pekerja">✕</button>}
      </div>
      <div className="av-worker-head">
        <span className="av-worker-ic">{kind === "ok" ? HELMET_OK : HELMET_OFF}</span>
        <div style={{ minWidth: 0 }}>
          <div className="av-worker-id">{id}</div>
          {subtitle && <div style={{ fontSize: 12, color: "var(--av-mute)" }}>{subtitle}</div>}
        </div>
      </div>
      {checked.map((it) => {
        const color = TONE[it.status];
        const status = it.status === "missing" ? "tidak dipakai" : it.status === "unknown" ? "tidak pasti" : "dipakai";
        return (
          <div className="av-meter" key={it.key}>
            <div><span>{PPE_LABEL[it.key]}</span><span style={{ color }}>{status}{fmtConf(it.confidence)}</span></div>
            {it.confidence != null && <div className="av-meter-track"><b style={{ width: `${Math.round(it.confidence * 100)}%`, background: color }} /></div>}
          </div>
        );
      })}
      {skipped.length > 0 && (
        <span style={{ fontSize: 12, color: "var(--av-mute)" }}>
          {skipped.join(" dan ").replace(/^./, (c) => c.toUpperCase())} tidak dicek di zona ini
        </span>
      )}
      {action}
    </section>
  );
}
