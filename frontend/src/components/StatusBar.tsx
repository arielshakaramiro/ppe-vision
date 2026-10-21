import { useEffect, useState } from "react";
import { api, fmtNum, type CameraHealth } from "../api";

export function StatusBar({ sys }: { sys: any }) {
  const [cams, setCams] = useState<CameraHealth[] | null>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const load = () => {
      api.camerasHealth().then(setCams).catch(() => {});
    };
    load();
    const t = setInterval(load, 5000);
    const clock = setInterval(() => setNow(new Date()), 1000);
    return () => { clearInterval(t); clearInterval(clock); };
  }, []);

  const lamp = !sys ? "tidak_ada_data" : sys.pipeline.running ? "baik" : "peringatan";
  const tanggal = now.toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "short", year: "numeric" });
  const jam = now.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }).replace(":", ".");

  const camStats = sys?.kamera.map((sk: any) => {
    const h = cams?.find((c) => c.kamera === sk.kamera);
    const statStr = h?.status === "peringatan" ? "perlu dicek" : h?.status === "baik" ? "baik" : "belum ada data";
    const fpsStr = sk.fps > 0 ? `${fmtNum(sk.fps)}×/dtk` : "";
    const txt = [h?.status === "peringatan" ? "buram" : "", fpsStr].filter(Boolean).join(", ");
    return { name: sk.kamera, status: h?.status || "tidak_ada_data", txt: txt || statStr };
  }) || [];

  return (
    <div className="status av-status">
      <span>
        <i className={`lamp ${lamp}`} />
        Pipeline<b>{!sys ? "–" : sys.pipeline.running ? "berjalan" : "berhenti"}</b>
      </span>
      {camStats.map((c: any) => (
        <span key={c.name}>
          <i className={`lamp ${c.status}`} />
          {c.name}<b>{c.txt}</b>
        </span>
      ))}
      {sys?.versi_model && <span>Model APD<b>{sys.versi_model}</b></span>}
      <span style={{ marginLeft: "auto", borderRight: 0 }}>
        {tanggal}<b>{jam} WIB</b>
      </span>
    </div>
  );
}
