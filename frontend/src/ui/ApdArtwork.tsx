type ApdArtworkProps = {
  items: string[];
  className?: string;
  preset?: boolean;
};

const helmet = (
  <>
    <path d="M3 16.5h18" />
    <path d="M5 16.5V14a7 7 0 0 1 14 0v2.5" />
    <path d="M12 7v4" />
  </>
);

const vest = <path d="M8.5 3 5 5.5V20h5v-6h4v6h5V5.5L15.5 3 12 7z" />;

const gloves = (
  <path d="M7.5 21v-6.5L5.2 10a1.4 1.4 0 0 1 2.4-1.4L9 10.5V5a1.3 1.3 0 0 1 2.6 0v5V4a1.3 1.3 0 0 1 2.6 0v6V5.2a1.3 1.3 0 0 1 2.6 0V15a6 6 0 0 1-2.3 4.7V21" />
);

const shoes = (
  <>
    <path d="M6 3h6v8.5l6.5 2.6A2.3 2.3 0 0 1 20 16.2V19H4V3z" />
    <path d="M4 16h16" />
  </>
);

const itemPaths: Record<string, React.ReactNode> = { helmet, vest, gloves, shoes };

export function ApdArtwork({ items, className = "apd-artwork", preset = false }: ApdArtworkProps) {
  const isComplete = items.length === 4;
  const isHelmetVest = items.length === 2 && items.includes("helmet") && items.includes("vest");

  if (isComplete) {
    return (
      <svg className={className} viewBox="0 0 80 80" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        <rect x="6" y="6" width="68" height="68" rx="16" strokeWidth="1" strokeDasharray="1.5 4" opacity=".5" />
        <g transform="translate(16 16) scale(.92)" strokeWidth="1.4">{helmet}</g>
        <g transform="translate(42 16) scale(.92)" strokeWidth="1.4">{vest}</g>
        <g transform="translate(16 42) scale(.92)" strokeWidth="1.4">{gloves}</g>
        <g transform="translate(42 42) scale(.92)" strokeWidth="1.4">{shoes}</g>
      </svg>
    );
  }

  if (isHelmetVest) {
    return (
      <svg className={className} viewBox="0 0 80 80" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        <circle cx="40" cy="40" r="30" strokeWidth="1" strokeDasharray="1.5 4" opacity=".5" />
        <g transform="translate(14 28) scale(1)" strokeWidth="1.3">{helmet}</g>
        <g transform="translate(42 28) scale(1)" strokeWidth="1.3">{vest}</g>
      </svg>
    );
  }

  const item = items[0];

  return (
    <svg className={className} viewBox="0 0 80 80" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <circle cx="40" cy="40" r="36" strokeWidth="1" opacity=".35" />
      <circle cx="40" cy="40" r="27" strokeWidth="1" strokeDasharray="1.5 4" opacity=".6" />
      <g transform={`translate(26.2 26.2) scale(${preset ? "1.0" : "1.15"})`} strokeWidth="1.3">{itemPaths[item]}</g>
      <path d="M40 2v6M40 72v6M2 40h6M72 40h6" strokeWidth="1" opacity=".45" />
    </svg>
  );
}
