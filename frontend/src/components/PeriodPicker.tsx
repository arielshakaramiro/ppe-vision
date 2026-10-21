export function PeriodPicker({ value, onChange, options }: { value: string; onChange: (p: string) => void; options: [string, string][] }) {
  return (
    <div className="seg">
      {options.map(([k, label]) => (
        <button key={k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>
          {label}
        </button>
      ))}
    </div>
  );
}
