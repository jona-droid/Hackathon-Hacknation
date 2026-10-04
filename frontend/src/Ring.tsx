/** Circular gauge: `value` of `total` (amber) with `secondary` of `total` on top (green). */
export function Ring({
  size = 46,
  stroke = 5,
  value,
  total,
  secondary = 0,
  children,
}: {
  size?: number;
  stroke?: number;
  value: number;
  total: number;
  secondary?: number;
  children?: React.ReactNode;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const arc = (v: number) => `${(c * Math.min(1, v / Math.max(1, total))).toFixed(2)} ${c.toFixed(2)}`;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink: 0 }}>
      <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(95,117,145,0.25)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#ffb547"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={arc(value)}
          style={{ transition: "stroke-dasharray 0.6s ease", filter: "drop-shadow(0 0 3px rgba(255,181,71,0.6))" }}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#3dffa2"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={arc(secondary)}
          style={{ transition: "stroke-dasharray 0.6s ease", filter: "drop-shadow(0 0 4px rgba(61,255,162,0.7))" }}
        />
      </g>
      {children}
    </svg>
  );
}
