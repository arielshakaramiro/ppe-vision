import { useEffect, useRef, useState } from "react";

const PLAYBACK_RATES = [0.25, 0.5, 0.75, 1];

export function SafeVideo({ src, className, poster, label, speedClassName = "" }: {
  src: string;
  className?: string;
  poster?: string;
  label: string;
  speedClassName?: string;
}) {
  const [playbackRate, setPlaybackRate] = useState(1);
  const videoRef = useRef<HTMLVideoElement>(null);

  const applyRate = (rate: number) => {
    const safe = Math.min(1, PLAYBACK_RATES.includes(rate) ? rate : 1);
    setPlaybackRate(safe);
    if (videoRef.current) {
      videoRef.current.defaultPlaybackRate = safe;
      videoRef.current.playbackRate = safe;
    }
  };

  useEffect(() => {
    applyRate(1);
  }, [src]);

  return (
    <>
      <video ref={videoRef} key={src} className={className} src={src} poster={poster} controls autoPlay muted loop
        playsInline preload="metadata" aria-label={label} onLoadedMetadata={() => applyRate(playbackRate)}
        onRateChange={(e) => {
          const actual = e.currentTarget.playbackRate;
          if (actual > 1 || !PLAYBACK_RATES.includes(actual)) applyRate(Math.min(actual, 1));
          else setPlaybackRate(actual);
        }} />
      <div className={`video-speed row gap wrap ${speedClassName}`.trim()}>
        <span className="muted small">Kecepatan putar</span>
        <div className="seg" aria-label={`Kecepatan ${label}`}>
          {PLAYBACK_RATES.map((rate) => (
            <button key={rate} className={playbackRate === rate ? "on" : ""} onClick={() => applyRate(rate)}>
              {rate}×
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
