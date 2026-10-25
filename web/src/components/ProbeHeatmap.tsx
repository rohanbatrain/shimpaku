// Probe heatmap: probe path (device → RPM test) × time bucket, coloured by loss / RTT / jitter,
// faults (SLE-bad probe-minutes) as diagonal stripes. Data: GET /api/probes/heatmap (stored RPM results).
import { useState, type MouseEvent } from "react";
import { Link } from "react-router";
import { useApi } from "../api";
import { Load, Seg } from "./ui";
import { time } from "../format";
import "../public.css";

export type Cell = { n: number; loss_pct: number | null; rtt_ms: number | null; jitter_ms: number | null; minutes: number; bad_minutes: number; classifier: string | null } | null;
export type Heat = {
  site: string; window_min: number; bucket_min: number; columns: number[]; source: string; demo?: boolean; replay?: boolean;
  thresholds: { loss_pct: number; rtt_ms: number }; paths: { id: string; node: string; test: string; label: string; n: number; cells: Cell[] }[];
};
export type Metric = "loss_pct" | "rtt_ms" | "jitter_ms";

// one hue, light -> dark (sequential); status colours are kept for the fault stripes
const RAMP = ["#e9f2ee", "#fbe0c2", "#f5b27c", "#e0733d", "#a8401c"];
const BINS: Record<Metric, { edges: number[]; unit: string; name: string }> = {
  loss_pct: { edges: [0, 1, 10, 50], unit: "%", name: "Loss" },
  rtt_ms: { edges: [10, 25, 50, 100], unit: " ms", name: "RTT" },
  jitter_ms: { edges: [1, 2, 5, 10], unit: " ms", name: "Jitter" },
};

function step(m: Metric, v: number | null): number | null {
  if (v == null) return null;
  const e = BINS[m].edges;
  if (m === "loss_pct" && v <= 0) return 0;
  let i = 0;
  while (i < e.length && v > e[i]) i++;
  return m === "loss_pct" ? Math.min(i, 4) : i;
}

export function legendItems(m: Metric): string[] {
  const e = BINS[m].edges, u = BINS[m].unit;
  if (m === "loss_pct") return ["0%", "≤ 1%", "1–10%", "10–50%", "> 50%"];
  return [`≤ ${e[0]}${u}`, `${e[0]}–${e[1]}${u}`, `${e[1]}–${e[2]}${u}`, `${e[2]}–${e[3]}${u}`, `> ${e[3]}${u}`];
}

const fmt = (v: number | null, u: string) => (v == null ? "not measured" : `${+v.toFixed(2)}${u}`);
const span = (b: number) => (b >= 60 ? `${b / 60} h` : `${b} min`);

function Tip({ h, p, i, x, y }: { h: Heat; p: Heat["paths"][number]; i: number; x: number; y: number }) {
  const c = p.cells[i];
  const t = h.columns[i];
  return (
    <div className="hm-tip" style={{ left: x, top: y }} role="tooltip">
      <b>{p.label}</b>
      <div className="weak">{time(t)}{h.bucket_min > 1 ? `–${time(t + h.bucket_min * 60)}` : ""} · {span(h.bucket_min)}</div>
      {c ? (
        <>
          <div>Loss {fmt(c.loss_pct, "%")} · RTT {fmt(c.rtt_ms, " ms")} · jitter {fmt(c.jitter_ms, " ms")}</div>
          <div className="weak">n = {c.n} result{c.n === 1 ? "" : "s"} · {c.bad_minutes} of {c.minutes} probe-minute{c.minutes === 1 ? "" : "s"} bad{c.classifier ? ` (${c.classifier})` : ""}</div>
        </>
      ) : <div className="weak">No probe result stored</div>}
    </div>
  );
}

export function HeatGrid({ h, metric, compact }: { h: Heat; metric: Metric; compact?: boolean }) {
  const [tip, setTip] = useState<{ p: number; i: number; x: number; y: number } | null>(null);
  const cols = h.columns.length;
  const move = (e: MouseEvent<HTMLDivElement>) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-i]");
    if (!el) return setTip(null);
    const box = e.currentTarget.getBoundingClientRect();
    const x = Math.min(e.clientX - box.left + 12, box.width - 236);
    setTip({ p: +el.dataset.p!, i: +el.dataset.i!, x: Math.max(0, x), y: e.clientY - box.top + 14 });
  };
  const ticks = [0, Math.floor(cols / 2), cols - 1].filter((v, k, a) => a.indexOf(v) === k && v >= 0);
  return (
    <div className={`hm${compact ? " compact" : ""}`} onMouseMove={move} onClick={move} onMouseLeave={() => setTip(null)}>
      <div className="hm-scroll" aria-hidden>
        <div className="hm-grid" style={{ gridTemplateColumns: `var(--hm-label) repeat(${cols}, minmax(var(--hm-min), 1fr))` }}>
          {h.paths.map((p, pi) => (
            <div className="hm-row" key={p.id} style={{ display: "contents" }}>
              <div className="hm-label" title={p.id}><span className="mono">{p.node}</span> → {p.test}</div>
              {p.cells.map((c, i) => {
                const s = step(metric, c ? c[metric] : null);
                return (
                  <div key={i} data-p={pi} data-i={i}
                    className={`hm-cell${c ? "" : " none"}${c && c.bad_minutes ? " fault" : ""}${c && s == null ? " nm" : ""}`}
                    style={c && s != null ? { backgroundColor: RAMP[s] } : undefined} />
                );
              })}
            </div>
          ))}
          <div />
          {Array.from({ length: cols }).map((_, i) => (
            <div key={i} className={`hm-tick${i === cols - 1 && cols > 1 ? " end" : ""}`}>{ticks.includes(i) ? time(h.columns[i]) : ""}</div>
          ))}
        </div>
      </div>
      {tip && <Tip h={h} p={h.paths[tip.p]} i={tip.i} x={tip.x} y={tip.y} />}
    </div>
  );
}

export function Legend({ metric, h }: { metric: Metric; h: Heat }) {
  return (
    <div className="hm-legend small">
      <span className="weak">{BINS[metric].name}</span>
      {legendItems(metric).map((l, i) => (
        <span key={l} className="hm-key"><i style={{ background: RAMP[i] }} />{l}</span>
      ))}
      <span className="hm-key"><i className="fault-key" />fault (SLE-bad minute)</span>
      <span className="hm-key"><i className="none-key" />no result</span>
      <span className="weak">· goal: loss ≤ {h.thresholds.loss_pct}%, RTT ≤ {h.thresholds.rtt_ms} ms · 1 cell = {span(h.bucket_min)}</span>
    </div>
  );
}

export function HeatTable({ h }: { h: Heat }) {
  return (
    <details className="hm-table">
      <summary>Table view ({h.paths.length} paths × {h.columns.length} buckets, cells with data)</summary>
      <div className="hm-table-scroll">
        <table>
          <thead><tr><th>Path</th><th>Start</th><th>n</th><th>Loss %</th><th>RTT ms</th><th>Jitter ms</th><th>Bad minutes</th></tr></thead>
          <tbody>
            {h.paths.flatMap((p) => p.cells.map((c, i) => c && (
              <tr key={p.id + i} className={c.bad_minutes ? "bad" : undefined}>
                <td>{p.label}</td><td className="mono">{time(h.columns[i])}</td><td>{c.n}</td>
                <td>{c.loss_pct ?? "—"}</td><td>{c.rtt_ms ?? "—"}</td><td>{c.jitter_ms ?? "—"}</td>
                <td>{c.bad_minutes}/{c.minutes}{c.classifier ? ` ${c.classifier}` : ""}</td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export const METRICS: { value: Metric; label: string }[] = [{ value: "loss_pct", label: "Loss" }, { value: "rtt_ms", label: "RTT" }, { value: "jitter_ms", label: "Jitter" }];

function summary(h: Heat) {
  const cells = h.paths.flatMap((p) => p.cells.filter(Boolean)) as NonNullable<Cell>[];
  const n = cells.reduce((a, c) => a + c.n, 0);
  const bad = cells.reduce((a, c) => a + c.bad_minutes, 0);
  const mins = cells.reduce((a, c) => a + c.minutes, 0);
  return `${h.paths.length} probe paths · ${n.toLocaleString()} results · ${bad} of ${mins.toLocaleString()} probe-minutes bad`;
}

/** Compact heatmap card for the Service levels page (same data, loss only). */
export default function ProbeHeatmapCard({ site, window }: { site: string; window: string }) {
  const [metric, setMetric] = useState<Metric>("loss_pct");
  const q = useApi<Heat>(`/api/probes/heatmap?site=${site}&window=${window}`);
  return (
    <div className="card">
      <div className="hm-head">
        <h2>Probe heatmap</h2>
        <span className="small weak">RPM paths × time, from stored results</span>
        <span className="spacer" />
        <Seg label="Colour by" value={metric} onChange={setMetric} options={METRICS} />
        <Link className="small" to={`/probes?site=${site}&window=${window}`}>Open →</Link>
      </div>
      <Load q={q} rows={3}>
        {(h) => h.paths.length === 0 ? <span className="small weak">No probe results stored for {site} in this window.</span> : (
          <>
            <p className="small weak hm-sum" aria-live="polite">{summary(h)}</p>
            <HeatGrid h={h} metric={metric} compact />
            <Legend metric={metric} h={h} />
          </>
        )}
      </Load>
    </div>
  );
}

export { summary as heatSummary };
