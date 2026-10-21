import { fmtTime, type Violation } from "../../../api";

export function ViolationRow({ v, onOpen }: { v: Violation; onOpen: (v: Violation) => void }) {
  return (
    <button className="vrow" onClick={() => onOpen(v)}>
      {v.foto_crop ? <img src={v.foto_crop} alt="" /> : <div className="thumb-empty" />}
      <div>
        <div className={v.jenis === "violation" ? "bad" : "warn"}>
          {v.jenis === "violation" ? `Tanpa ${v.apd_kurang.join(", ")}` : `Tidak pasti: ${v.apd_tidak_pasti.join(", ")}`}
        </div>
        <div className="muted small">
          {v.pekerja} · {v.kamera} · {fmtTime(v.waktu)}
        </div>
      </div>
    </button>
  );
}
