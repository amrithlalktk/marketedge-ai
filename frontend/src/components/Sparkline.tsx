/** Tiny dependency-free SVG sparkline. Colour follows first→last direction unless `tone` is given. */
export function Sparkline({ values, width = 120, height = 32, tone, label, domain }: { values: number[]; width?: number; height?: number; tone?: "up" | "down" | "neutral"; label?: string; domain?: [number, number] }) {
  const v = values.filter((x) => typeof x === "number" && Number.isFinite(x));
  if (v.length < 2) return <svg width={width} height={height} aria-hidden />;
  const lo = domain ? domain[0] : Math.min(...v);
  const hi = domain ? domain[1] : Math.max(...v);
  const span = hi - lo || 1;
  const pts = v.map((y, i) => `${((i / (v.length - 1)) * width).toFixed(1)},${(height - 2 - ((y - lo) / span) * (height - 4)).toFixed(1)}`);
  const t = tone ?? (v[v.length - 1] >= v[0] ? "up" : "down");
  const color = t === "up" ? "#22c55e" : t === "down" ? "#f05252" : "#60a5fa";
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={label ?? "Trend sparkline"} className="block max-w-full">
      <polyline fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" points={pts.join(" ")} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
