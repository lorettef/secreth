export function Seal({ compact = false }: { compact?: boolean }) {
  return (
    <svg
      className={compact ? "seal seal--compact" : "seal"}
      viewBox="0 0 120 120"
      role="img"
      aria-label="Печать Министерства культуры с перекрещёнными кистями"
    >
      <circle cx="60" cy="60" r="55" fill="none" stroke="currentColor" strokeWidth="3" />
      <circle cx="60" cy="60" r="45" fill="none" stroke="currentColor" strokeWidth="1" opacity=".45" />
      <g transform="rotate(42 60 60)">
        <rect x="56" y="24" width="8" height="63" rx="4" fill="currentColor" />
        <path d="M53 88h14l-3 14h-8z" fill="#DF4555" />
        <path d="M56 22l4-10 4 10z" fill="#287DCE" />
      </g>
      <g transform="rotate(-42 60 60)">
        <rect x="56" y="24" width="8" height="63" rx="4" fill="currentColor" />
        <path d="M53 88h14l-3 14h-8z" fill="#287DCE" />
        <path d="M56 22l4-10 4 10z" fill="#DF4555" />
      </g>
      <circle cx="60" cy="60" r="7" fill="#F4F6F8" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "brand brand--compact" : "brand"}>
      <Seal compact={compact} />
      <div>
        <div className="brand__name">Министерство культуры</div>
        <div className="brand__tagline">Школа 21 · Якутск</div>
      </div>
    </div>
  );
}
