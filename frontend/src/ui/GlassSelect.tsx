import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Pengganti <select> bawaan browser, bergaya glass + animasi.
 *
 * Dibuat supaya bisa ditukar langsung dengan <select> lama TANPA mengubah logika:
 *   <select value={kamera} onChange={(e) => setKamera(e.target.value)}>…</select>
 * menjadi
 *   <GlassSelect value={kamera} onChange={setKamera} options={[…]} ariaLabel="Kamera" />
 *
 * Panel dirender lewat portal ke <body> dengan posisi fixed, jadi tidak terpotong
 * oleh kartu yang memakai overflow:hidden. Mendukung keyboard (panah, Enter, Esc, Home/End).
 */
export type GlassOption = {
  value: string;
  label: ReactNode;
  /** teks kecil di bawah label, misalnya nama file rekaman */
  hint?: ReactNode;
  /** judul kelompok; opsi dengan group sama dikelompokkan berurutan */
  group?: string;
  disabled?: boolean;
};

type Props = {
  value: string;
  onChange: (value: string) => void;
  options: GlassOption[];
  ariaLabel: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  /** isi tombol khusus (misalnya thumbnail + status) — default: label opsi terpilih */
  renderValue?: (selected: GlassOption | undefined) => ReactNode;
  /** lebar minimal panel (px). Default: selebar tombol. */
  panelMinWidth?: number;
};

export function GlassSelect({ value, onChange, options, ariaLabel, placeholder = "Pilih…", disabled, className, renderValue, panelMinWidth }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [pos, setPos] = useState<{ left: number; top: number; width: number; up: boolean } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const pan = useRef<HTMLDivElement>(null);
  const listId = useId();

  const selectable = useMemo(() => options.map((o, i) => ({ o, i })).filter(({ o }) => !o.disabled), [options]);
  const selected = options.find((o) => o.value === value);

  const place = useCallback(() => {
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const spaceBelow = window.innerHeight - r.bottom;
    const up = spaceBelow < 260 && r.top > spaceBelow;
    setPos({ left: r.left, top: up ? r.top - 8 : r.bottom + 8, width: Math.max(r.width, panelMinWidth ?? 0), up });
  }, [panelMinWidth]);

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const onMove = () => place();
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
    return () => {
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
    };
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (btn.current?.contains(t) || pan.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const openPanel = () => {
    if (disabled) return;
    const idx = options.findIndex((o) => o.value === value);
    setActive(idx >= 0 ? idx : selectable[0]?.i ?? -1);
    setOpen(true);
  };
  const choose = (o: GlassOption) => {
    if (o.disabled) return;
    setOpen(false);
    btn.current?.focus();
    if (o.value !== value) onChange(o.value);
  };
  const move = (dir: 1 | -1) => {
    if (!selectable.length) return;
    const k = selectable.findIndex(({ i }) => i === active);
    const next = selectable[(k + dir + selectable.length) % selectable.length];
    setActive(next.i);
  };

  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); openPanel(); }
      return;
    }
    if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
    else if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Home") { e.preventDefault(); if (selectable[0]) setActive(selectable[0].i); }
    else if (e.key === "End") { e.preventDefault(); const l = selectable[selectable.length - 1]; if (l) setActive(l.i); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); const o = options[active]; if (o) choose(o); }
    else if (e.key === "Tab") setOpen(false);
  };

  useEffect(() => {
    if (!open || active < 0) return;
    pan.current?.querySelector<HTMLElement>(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  let lastGroup: string | undefined;
  return (
    <div className={`av-select ${className ?? ""}`}>
      <button
        ref={btn}
        type="button"
        className="av-select-btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openPanel())}
        onKeyDown={onKey}
      >
        <span className="av-select-val">{renderValue ? renderValue(selected) : selected?.label ?? <span className="av-muted">{placeholder}</span>}</span>
        <svg className="av-select-chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
      </button>
      {open && pos && createPortal(
        <div
          ref={pan}
          id={listId}
          role="listbox"
          aria-label={ariaLabel}
          className={`av-pop ${pos.up ? "up" : ""}`}
          style={{
            position: "fixed", left: pos.left, minWidth: pos.width, maxWidth: "min(460px, calc(100vw - 32px))", maxHeight: 320, overflow: "auto", zIndex: 1000,
            ...(pos.up ? { bottom: window.innerHeight - pos.top, transformOrigin: "bottom left" } : { top: pos.top, transformOrigin: "top left" }),
          }}
        >
          {options.map((o, i) => {
            const head = o.group && o.group !== lastGroup ? <div className="av-pop-head" key={`g-${o.group}-${i}`}>{o.group}</div> : null;
            lastGroup = o.group;
            const on = o.value === value;
            return [
              head,
              <button
                key={o.value}
                id={`${listId}-${i}`}
                data-i={i}
                type="button"
                role="option"
                aria-selected={on}
                aria-disabled={o.disabled || undefined}
                tabIndex={-1}
                className={`av-pop-item ${i === active ? "active" : ""}`}
                style={o.disabled ? { opacity: 0.4, cursor: "default" } : undefined}
                onMouseEnter={() => !o.disabled && setActive(i)}
                onClick={() => choose(o)}
              >
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: "block" }}>{o.label}</span>
                  {o.hint && <span className="av-muted" style={{ display: "block", fontSize: 12 }}>{o.hint}</span>}
                </span>
                <span className="av-check" aria-hidden="true">{on ? "✓" : ""}</span>
              </button>,
            ];
          })}
        </div>,
        document.body,
      )}
    </div>
  );
}
