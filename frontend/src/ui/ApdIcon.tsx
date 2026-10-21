type ApdIconProps = { item: string; className?: string };

export function ApdIcon({ item, className = "apd-icon" }: ApdIconProps) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 2.2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      {item === "helmet" && (
        <>
          <path {...common} d="M7 17a9 9 0 0 1 18 0" />
          <path {...common} d="M5 19h22" />
          <path {...common} d="M11 18v-5M16 18V9M21 18v-5" />
          <path {...common} d="M9 20c1 4 4 6 7 6s6-2 7-6" />
        </>
      )}
      {item === "vest" && (
        <>
          <path {...common} d="M11 5h10l5 7v15H6V12l5-7Z" />
          <path {...common} d="M12 6c1 4 3 6 4 6s3-2 4-6" />
          <path {...common} d="M16 12v15" />
          <path {...common} d="M10 18h4M18 18h4M10 23h4M18 23h4" />
        </>
      )}
      {item === "gloves" && (
        <>
          <path {...common} d="M11 18V8a2 2 0 0 1 4 0v8" />
          <path {...common} d="M15 17V7a2 2 0 0 1 4 0v10" />
          <path {...common} d="M19 17v-7a2 2 0 0 1 4 0v8" />
          <path {...common} d="M11 14 8 11a2 2 0 0 0-3 3l5 8c2 4 5 5 9 5h1c4 0 7-3 7-7v-5" />
        </>
      )}
      {item === "shoes" && (
        <>
          <path {...common} d="M7 19c4 0 7-2 9-6l4 5 5 2c2 1 3 3 2 5H6c-1-2-1-4 1-6Z" />
          <path {...common} d="M9 19c0 2 2 3 5 3h12" />
          <path {...common} d="M16 14l-3-2M18 16l-3-2M20 18l-3-2" />
        </>
      )}
      {!item && <circle {...common} cx="16" cy="16" r="5" />}
    </svg>
  );
}
