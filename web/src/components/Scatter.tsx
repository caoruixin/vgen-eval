import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../i18n";
import type { LeaderRow } from "../types";
import { pct, usd, vendorColor } from "../util";

const H = 320;
const M = { top: 28, right: 28, bottom: 40, left: 52 };

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(760);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((entries) => setW(Math.max(320, Math.floor(entries[0].contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** Round tick step (1 / 2 / 2.5 / 5 × 10^k) giving about four intervals. */
function niceStep(max: number): number {
  const raw = Math.max(max, 1e-6) / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  for (const s of [1, 2, 2.5, 5, 10]) if (s * mag >= raw) return s * mag;
  return 10 * mag;
}

/** Quality (y) vs cost per clip (x). One point per model, coloured by vendor; frontier joined by a line. */
export default function Scatter({ rows }: { rows: LeaderRow[] }) {
  const { t } = useI18n();
  const [box, W] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const pts = rows.filter((r) => r.quality !== null && r.cost_median !== null);
  const step = niceStep(Math.max(0.1, ...pts.map((p) => p.cost_median as number)) * 1.08);
  const xMax = step * Math.ceil((Math.max(0.1, ...pts.map((p) => p.cost_median as number)) * 1.08) / step);
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;
  const x = (v: number) => M.left + (v / xMax) * iw;
  const y = (v: number) => M.top + (1 - v) * ih;
  const frontier = useMemo(() => pts.filter((p) => p.pareto).sort((a, b) => (a.cost_median as number) - (b.cost_median as number)), [pts]);
  const xTicks = Array.from({ length: Math.round(xMax / step) + 1 }, (_, i) => +(step * i).toFixed(6));
  const yTicks = [0, 0.25, 0.5, 0.75, 1];
  const vendors = [...new Set(pts.map((p) => p.vendor))];
  const hovered = pts.find((p) => p.model === hover);

  if (!pts.length) return null;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-4 text-xs text-ink-2">
        {vendors.map((v) => (
          <span key={v} className="flex items-center gap-1.5">
            <span className="vendor-dot" style={{ background: vendorColor(v) }} /> {v}
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <svg width="18" height="6" aria-hidden>
            <line x1="0" y1="3" x2="18" y2="3" stroke="var(--ink-2)" strokeWidth="2" strokeLinecap="round" />
          </svg>
          {t("lb.frontier")}
        </span>
      </div>
      <div className="relative" ref={box}>
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block" role="img" aria-label={t("lb.scatter")}>
          {yTicks.map((v) => (
            <g key={`y${v}`}>
              <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} stroke="var(--grid)" strokeWidth="1" />
              <text x={M.left - 8} y={y(v)} textAnchor="end" dominantBaseline="middle" fontSize="11" fill="var(--muted)" className="tnum">
                {pct(v)}
              </text>
            </g>
          ))}
          {xTicks.map((v) => (
            <g key={`x${v}`}>
              <line x1={x(v)} x2={x(v)} y1={M.top} y2={H - M.bottom} stroke="var(--grid)" strokeWidth="1" />
              <text x={x(v)} y={H - M.bottom + 16} textAnchor="middle" fontSize="11" fill="var(--muted)" className="tnum">
                {usd(v)}
              </text>
            </g>
          ))}
          <line x1={M.left} x2={W - M.right} y1={H - M.bottom} y2={H - M.bottom} stroke="var(--axis)" strokeWidth="1" />
          <text x={W - M.right} y={H - 6} textAnchor="end" fontSize="11" fill="var(--ink-2)">
            {t("lb.cost")} →
          </text>
          <text x={M.left} y={12} textAnchor="start" fontSize="11" fill="var(--ink-2)">
            ↑ {t("lb.quality")}
          </text>
          {frontier.length > 1 && (
            <polyline
              points={frontier.map((p) => `${x(p.cost_median as number)},${y(p.quality as number)}`).join(" ")}
              fill="none"
              stroke="var(--ink-2)"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
              opacity="0.6"
            />
          )}
          {pts.map((p) => {
            const cx = x(p.cost_median as number);
            const cy = y(p.quality as number);
            const on = hover === p.model;
            return (
              <g key={p.model} onPointerEnter={() => setHover(p.model)} onPointerLeave={() => setHover(null)} onFocus={() => setHover(p.model)} onBlur={() => setHover(null)} tabIndex={0} style={{ outline: "none" }}>
                <circle cx={cx} cy={cy} r="14" fill="transparent" />
                <circle cx={cx} cy={cy} r={on ? 7 : 5.5} fill={vendorColor(p.vendor)} stroke="var(--surface)" strokeWidth="2" />
                {(p.pareto || on) && (
                  <text x={cx + 10} y={cy - 8} fontSize="11.5" fill="var(--ink)" fontWeight={on ? 600 : 500}>
                    {p.label}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {hovered && (
          <div
            className="card pointer-events-none absolute z-10 px-3 py-2 text-xs shadow-lg"
            style={{ left: x(hovered.cost_median as number), top: y(hovered.quality as number), transform: x(hovered.cost_median as number) > W - 200 ? "translate(calc(-100% - 12px), 12px)" : "translate(12px, 12px)" }}
          >
            <div className="tnum text-[13px] font-semibold">
              {pct(hovered.quality)} · {usd(hovered.cost_median)}
            </div>
            <div className="flex items-center gap-1.5 text-ink-2">
              <svg width="12" height="4" aria-hidden>
                <line x1="0" y1="2" x2="12" y2="2" stroke={vendorColor(hovered.vendor)} strokeWidth="3" strokeLinecap="round" />
              </svg>
              {hovered.label}
            </div>
            <div className="text-muted">
              {hovered.quality_source === "human" ? `${t("lb.src.human")} n=${hovered.rated_n}` : `${t("lb.src.judge")} n=${hovered.judge_n}`}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
