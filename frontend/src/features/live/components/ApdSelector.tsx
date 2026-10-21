import { useEffect, useState } from "react";
import { api, can, ITEM_LABEL, type Zone } from "../../../api";
import { useMe } from "../../../session";
import { ApdIcon, ApdArtwork } from "../../../ui";

const PRESETS = [
  { label: "Demo: helm + rompi", req: ["helmet", "vest"] },
  { label: "Lengkap", req: ["helmet", "vest", "gloves", "shoes"] },
  { label: "Helm saja", req: ["helmet"] },
  { label: "Rompi saja", req: ["vest"] },
  { label: "Sarung tangan saja", req: ["gloves"] },
  { label: "Sepatu saja", req: ["shoes"] },
];

const COMPACT_PRESETS = [
  { label: "Lengkap", req: ["helmet", "vest", "gloves", "shoes"] },
  { label: "Helm + rompi", req: ["helmet", "vest"] },
  { label: "Helm saja", req: ["helmet"] },
  { label: "Rompi saja", req: ["vest"] },
];

export function ApdSelector({ cameraId, compact = false }: { cameraId?: string; compact?: boolean }) {
  const me = useMe();
  const canEdit = can(me, "supervisor");
  const [zones, setZones] = useState<Zone[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Zona yang berlaku untuk kamera ini (khusus kamera + global). Tanpa cameraId = semua zona.
  const load = () => api.zones(cameraId).then(setZones).catch(() => {});
  useEffect(() => { load(); }, [cameraId]);

  // Utamakan zona khusus kamera; jika tidak ada, pakai zona global (camera_id kosong).
  const cur = zones.find((z) => cameraId && z.camera_id === cameraId) ?? zones[0];
  if (!cur) return null;

  const req = new Set(cur.required);
  const toggle = async (item: string) => {
    if (!canEdit || busy) return;
    const next = new Set(req);
    if (next.has(item)) next.delete(item);
    else next.add(item);
    if (next.size === 0) return alert("Minimal satu APD wajib dipilih.");
    setBusy(true);
    setError("");
    await api.updateZoneRequired(cur.name, Array.from(next), cur.camera_id).then(load).catch((reason: Error) => setError(reason.message)).finally(() => setBusy(false));
  };
  const setPreset = async (items: string[]) => {
    if (!canEdit || busy) return;
    setBusy(true);
    setError("");
    await api.updateZoneRequired(cur.name, items, cur.camera_id).then(load).catch((reason: Error) => setError(reason.message)).finally(() => setBusy(false));
  };

  return (
    <div className={`card panel apd-selector${compact ? " apd-selector-compact" : ""}`}>
      <h3>APD yang dicek di {cur.name}</h3>
      <div className="apd-selector-items">
        {Object.entries(ITEM_LABEL).map(([k, label]) => {
          const on = req.has(k);
          return (
            <button key={k} className={`sign ${on ? "on" : ""}`} disabled={!canEdit || busy} onClick={() => toggle(k)}>
              <div className="disc">{compact ? <ApdArtwork items={[k]} /> : <ApdIcon item={k} />}</div>
              <div className="nm">{label}</div>
              <div className="st">{on ? "Wajib" : "Diabaikan"}</div>
            </button>
          );
        })}
      </div>
      {canEdit && (
        <div className="row gap wrap apd-selector-presets">
          {(compact ? COMPACT_PRESETS : PRESETS).map((p) => {
            const on = p.req.length === req.size && p.req.every((x) => req.has(x));
            return (
              <button key={p.label} className={`preset ${on ? "on" : ""}`} disabled={busy} onClick={() => setPreset(p.req)}>
                {compact && <ApdArtwork items={p.req} className="apd-preset-artwork" preset />}
                <span>{p.label}</span>
              </button>
            );
          })}
        </div>
      )}
      <span className="sr-only" role="status" aria-live="polite">{busy ? "Menyimpan aturan APD" : error ? `Gagal menyimpan aturan APD: ${error}` : ""}</span>
    </div>
  );
}
