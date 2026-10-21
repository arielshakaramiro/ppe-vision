import { useEffect, useState, type RefObject } from "react";

/**
 * Kontrol kecepatan putar 0,25× – 1× untuk <video>.
 * Pakai: <div className="av-stage"><video ref={vid} …/><SpeedControl videoRef={vid} floating /></div>
 * Kecepatan dipasang ulang saat video berganti sumber (loadedmetadata), jadi tidak kembali ke 1×.
 */
const SPEEDS = [0.25, 0.5, 0.75, 1] as const;

export function SpeedControl({ videoRef, floating, label = "Kecepatan putar", onChange }: {
  videoRef: RefObject<HTMLVideoElement>;
  floating?: boolean;
  label?: string;
  onChange?: (rate: number) => void;
}) {
  const [rate, setRate] = useState(1);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    v.playbackRate = rate;
    const keep = () => { v.playbackRate = rate; };
    v.addEventListener("loadedmetadata", keep);
    return () => v.removeEventListener("loadedmetadata", keep);
  }, [rate, videoRef]);

  return (
    <div className={`av-speed ${floating ? "floating" : ""}`}>
      <span>{label}</span>
      <div className="av-seg light" role="group" aria-label={label}>
        {SPEEDS.map((r) => (
          <button key={r} type="button" aria-pressed={r === rate} onClick={() => { setRate(r); onChange?.(r); }}>
            {String(r).replace(".", ",")}×
          </button>
        ))}
      </div>
    </div>
  );
}
