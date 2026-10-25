"use client";
import { useId, useState } from "react";

export type Series = { name: string; color: string; points: { x: string; y: number }[]; dashed?: boolean };
export default function Chart({ series, currency = false, height = 220 }: { series: Series[]; currency?: boolean; height?: number }) {
  const id = useId().replace(/:/g, "");
  const [hover, setHover] = useState<number | null>(null);
  const all = series.flatMap(s => s.points);
  if (all.length < 2) return <div className="chart-empty" style={{ height }}>History will appear as quotes are collected.</div>;
  const width = 740, top = 15, left = 55, right = 18, bottom = 30;
  const xs = all.map(p => new Date(p.x).getTime()), ys = all.map(p => p.y);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const pad = Math.max((Math.max(...ys) - Math.min(...ys)) * .16, currency ? 10 : .025);
  const yMin = Math.min(...ys) - pad, yMax = Math.max(...ys) + pad;
  const sx = (v: string | number) => left + ((typeof v === "string" ? new Date(v).getTime() : v) - xMin) / Math.max(1, xMax - xMin) * (width - left - right);
  const sy = (v: number) => top + (1 - (v - yMin) / (yMax - yMin)) * (height - top - bottom);
  const path = (points: Series["points"]) => points.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(2)},${sy(p.y).toFixed(2)}`).join(" ");
  const dates = [...new Set(all.map(p => p.x))].sort();
  const selected = hover === null ? null : dates.reduce((a, b) => Math.abs(sx(a) - hover) < Math.abs(sx(b) - hover) ? a : b);
  const selectedSeries = selected ? series.map(s => ({ ...s, point: s.points.reduce<{ x: string; y: number } | undefined>((best, p) => !best || Math.abs(new Date(p.x).getTime()-new Date(selected).getTime()) < Math.abs(new Date(best.x).getTime()-new Date(selected).getTime()) ? p : best, undefined) })) : [];
  return <div className="chart-wrap">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={currency ? "Realized and predicted profit over time" : "Decimal odds history by venue"}
      onMouseMove={e => { const box = e.currentTarget.getBoundingClientRect(); setHover(Math.max(left, Math.min(width - right, (e.clientX - box.left) / box.width * width))); }} onMouseLeave={() => setHover(null)}>
      <defs><linearGradient id={`fill-${id}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={series[0]?.color} stopOpacity=".13" /><stop offset="100%" stopColor={series[0]?.color} stopOpacity="0" /></linearGradient></defs>
      {[0, 1, 2, 3, 4].map(i => { const y = top + (height - top - bottom) * i / 4; const value = yMax - (yMax - yMin) * i / 4; return <g key={i}><line x1={left} x2={width-right} y1={y} y2={y} stroke="#263031" strokeDasharray="3 5" /><text x={left-12} y={y+4} textAnchor="end" fill="#738080" fontSize="10" fontFamily="monospace">{currency ? `${value < 0 ? "−" : ""}$${Math.abs(value).toFixed(0)}` : value.toFixed(2)}</text></g>; })}
      {series[0] && <path d={`${path(series[0].points)} L${sx(series[0].points.at(-1)!.x)},${height-bottom} L${sx(series[0].points[0].x)},${height-bottom} Z`} fill={`url(#fill-${id})`} />}
      {series.map(s => <path key={s.name} d={path(s.points)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.dashed ? "4 4" : undefined} />)}
      {[0, 1, 2, 3, 4].map(i => { const v = xMin + (xMax-xMin)*i/4; return <text key={i} x={sx(v)} y={height-5} textAnchor="middle" fill="#738080" fontSize="10" fontFamily="monospace">{new Date(v).toLocaleString("en-US", currency ? { month: "short", day: "numeric" } : { hour: "2-digit", minute: "2-digit", hour12: false })}</text>; })}
      {selected && <><line x1={sx(selected)} x2={sx(selected)} y1={top} y2={height-bottom} stroke="#788889" strokeDasharray="3 3" />{selectedSeries.map(s => s.point && <circle key={s.name} cx={sx(s.point.x)} cy={sy(s.point.y)} r="4" fill={s.color} stroke="#11191a" strokeWidth="2" />)}</>}
    </svg>
    {selected && <div className="chart-tooltip" style={{ left: `${Math.min(72, Math.max(10, sx(selected)/width*100))}%` }}><span>{new Date(selected).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>{selectedSeries.map(s => s.point && <div key={s.name}><i style={{ background: s.color }} />{s.name}<strong>{currency ? `$${s.point.y.toFixed(2)}` : s.point.y.toFixed(3)}</strong></div>)}</div>}
    <div className="chart-legend">{series.map(s => <span key={s.name}><i style={{ background: s.color }} />{s.name}</span>)}</div>
  </div>;
}
