export function Kpi({ label, value, hint, tone }: { label: string; value: string | number; hint?: string; tone?: "bad" | "good" }) {
  return (
    <div className="card kpi">
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value ${tone ?? ""}`}>{value}</div>
      {hint && <div className="muted small">{hint}</div>}
    </div>
  );
}
