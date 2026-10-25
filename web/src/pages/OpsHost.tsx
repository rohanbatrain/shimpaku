import { useEffect } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../api";
import TopBar from "../components/TopBar";
import { Load, Pill } from "../components/ui";
import { time } from "../format";
import "../ops.css";

type Sample = { t: number; temp_c: number | null; temp_source: string | null; temp_crit_c: number | null; throttle: { core: number; package: number } | null; fans: { label: string; rpm: number }[]; ncpu: number | null; load: number[] | null; mem_total_mb: number | null; mem_avail_mb: number | null; vms_running?: number };
type Point = { t: number; temp_c: number | null; load1: number | null; mem_pct: number | null; throttle: number | null };
type Host = {
  demo: boolean; sampler: boolean; latest: Sample | null; series: Point[]; warn_c: number;
  vms: { vms: { id: string | null; name: string; state: string }[]; error: string | null; t: number | null };
  guard: { enabled: boolean; threshold_c: number; hold_s: number; events: { t: number; temp_c: number; held_s: number }[] };
};

/** Half-ring gauge: value against max, coloured by thresholds. */
function Gauge({ label, value, max, unit, warn, crit, sub }: { label: string; value: number | null; max: number; unit: string; warn: number; crit: number; sub?: string }) {
  const f = value == null ? 0 : Math.max(0, Math.min(1, value / max));
  const tone = value == null ? "none" : value >= crit ? "crit" : value >= warn ? "warn" : "ok";
  const r = 42, c = Math.PI * r;
  return (
    <div className={`gauge g-${tone}`} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={value ?? undefined}>
      <svg viewBox="0 0 100 58" aria-hidden>
        <path d="M8 52 A42 42 0 0 1 92 52" className="g-track" />
        <path d="M8 52 A42 42 0 0 1 92 52" className="g-val" strokeDasharray={`${f * c} ${c}`} />
      </svg>
      <div className="g-num">{value == null ? "—" : value}<span>{value == null ? "" : unit}</span></div>
      <div className="g-label">{label}</div>
      {sub && <div className="g-sub">{sub}</div>}
    </div>
  );
}

const tip = (fmt: (v: number) => string) => ({ active, payload, label }: any) =>
  active && payload?.length ? (
    <div className="tooltip"><div style={{ opacity: 0.75 }}>{time(label)}</div>{payload.map((p: any) => <div key={p.dataKey}>{p.name}: <b>{p.value == null ? "—" : fmt(p.value)}</b></div>)}</div>
  ) : null;

function Spark({ data, k, name, color, unit, domain, refY, height = 130 }: { data: Point[]; k: keyof Point; name: string; color: string; unit: string; domain?: [number, number]; refY?: number; height?: number }) {
  return (
    <div role="img" aria-label={`${name}, last 24 hours`} style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
          <defs><linearGradient id={`g-${k}`} x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={color} stopOpacity={0.3} /><stop offset="1" stopColor={color} stopOpacity={0.03} /></linearGradient></defs>
          <CartesianGrid vertical={false} stroke="#EEF1F0" />
          <XAxis dataKey="t" tickFormatter={time} tick={{ fontSize: 11, fill: "#56645E" }} minTickGap={50} axisLine={{ stroke: "#DCE2DF" }} tickLine={false} />
          <YAxis domain={domain ?? ["auto", "auto"]} tick={{ fontSize: 11, fill: "#56645E" }} axisLine={false} tickLine={false} />
          {refY != null && <ReferenceLine y={refY} stroke="#B3261E" strokeDasharray="4 3" />}
          <Tooltip content={tip((v) => `${v}${unit}`) as any} cursor={{ stroke: "#56645E", strokeWidth: 1 }} />
          <Area type="monotone" dataKey={k} name={name} stroke={color} strokeWidth={2} fill={`url(#g-${k})`} isAnimationActive={false} connectNulls />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

const stateTone = (s: string) => (s === "running" ? "ok" : s === "paused" || s === "in shutdown" ? "warn" : s === "crashed" ? "crit" : "neutral");

export default function OpsHost() {
  const q = useApi<Host>("/api/ops/host");
  useEffect(() => { const t = setInterval(q.reload, 15000); return () => clearInterval(t); }, [q.reload]);
  return (
    <>
      <TopBar title="Lab host" crumbs="Admin · sampled every 15 s, no root · last 24 h" />
      <div className="content">
        <Load q={q} rows={6}>
          {(h) => {
            const s = h.latest;
            const temp = s?.temp_c ?? null;
            const memPct = s?.mem_total_mb ? Math.round(100 * (1 - (s.mem_avail_mb ?? 0) / s.mem_total_mb)) : null;
            const load1 = s?.load?.[0] ?? null;
            const thr24 = h.series.reduce((a, p) => a + (p.throttle ?? 0), 0);
            const crit = s?.temp_crit_c ?? 100;
            return (
              <>
                {temp != null && temp >= h.warn_c && (
                  <div className="notice crit ops-banner" role="alert">
                    <b>CPU package at {temp} °C</b> (critical {crit} °C). The host crashed from heat on 2026-10-25 at 94–98 °C. Cool it now: shut off VMs you are not using, check the vents and fan, or lower the load.
                  </div>
                )}
                {!h.sampler && <div className="notice warn">The sampler is not running in this process (API started alone): this is one live reading with no history.</div>}
                <div className="gauges card">
                  <Gauge label="CPU package" value={temp} max={crit} unit="°C" warn={h.warn_c - 10} crit={h.warn_c} sub={s?.temp_source ? `${s.temp_source} · crit ${crit} °C` : "no sensor found"} />
                  <Gauge label="Load (1 min)" value={load1} max={s?.ncpu ?? 16} unit="" warn={(s?.ncpu ?? 16) * 0.7} crit={s?.ncpu ?? 16} sub={s?.load ? `${s.load[1]} · ${s.load[2]} (5/15 min) · ${s.ncpu ?? "?"} CPUs` : undefined} />
                  <Gauge label="Memory used" value={memPct} max={100} unit="%" warn={85} crit={95} sub={s?.mem_total_mb ? `${((s.mem_avail_mb ?? 0) / 1024).toFixed(1)} of ${(s.mem_total_mb / 1024).toFixed(1)} GiB free` : undefined} />
                  <div className="g-stat">
                    <div className="g-stat-n">{thr24.toLocaleString()}</div>
                    <div className="g-label">Throttle events, 24 h</div>
                    <div className="g-sub">{s?.throttle ? `since boot: ${s.throttle.package.toLocaleString()} package · ${s.throttle.core.toLocaleString()} core` : "counters not exposed"}</div>
                    <div className="g-sub">{s?.fans.length ? s.fans.map((f) => `${f.label} ${f.rpm} rpm`).join(" · ") : "fan speed not exposed"}</div>
                  </div>
                </div>
                <div className="grid2">
                  <div className="card"><h2>CPU package °C</h2><Spark data={h.series} k="temp_c" name="Package" color="#D9622B" unit=" °C" refY={h.warn_c} /></div>
                  <div className="card"><h2>Throttle events per interval</h2>
                    <div role="img" aria-label="Throttle events, last 24 hours" style={{ width: "100%", height: 130 }}>
                      <ResponsiveContainer>
                        <BarChart data={h.series} margin={{ top: 6, right: 8, bottom: 0, left: -10 }} barCategoryGap={1}>
                          <CartesianGrid vertical={false} stroke="#EEF1F0" />
                          <XAxis dataKey="t" tickFormatter={time} tick={{ fontSize: 11, fill: "#56645E" }} minTickGap={50} axisLine={{ stroke: "#DCE2DF" }} tickLine={false} />
                          <YAxis tick={{ fontSize: 11, fill: "#56645E" }} axisLine={false} tickLine={false} allowDecimals={false} />
                          <Tooltip content={tip((v) => v.toLocaleString()) as any} cursor={{ fill: "rgba(179,38,30,.06)" }} />
                          <Bar dataKey="throttle" name="Events" fill="#B3261E" isAnimationActive={false} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                  <div className="card"><h2>Load (1 min)</h2><Spark data={h.series} k="load1" name="Load" color="#1D6E8C" unit="" /></div>
                  <div className="card"><h2>Memory used %</h2><Spark data={h.series} k="mem_pct" name="Memory" color="#01A982" unit="%" domain={[0, 100]} /></div>
                </div>
                <div className="grid2">
                  <div className="card flush">
                    <div className="card-h"><h2>Virtual machines</h2><span className="spacer" /><span className="small weak">{h.vms.vms.filter((v) => v.state === "running").length} of {h.vms.vms.length} running</span></div>
                    {h.vms.error ? <div className="notice warn ops-inset">virsh: {h.vms.error}</div> : (
                      <table className="t"><thead><tr><th>Name</th><th>Id</th><th>State</th></tr></thead>
                        <tbody>{h.vms.vms.map((v) => <tr key={v.name}><td className="mono">{v.name}</td><td className="mono weak">{v.id ?? "—"}</td><td><Pill tone={stateTone(v.state)}>{v.state}</Pill></td></tr>)}</tbody>
                      </table>
                    )}
                  </div>
                  <div className="card">
                    <h2>Thermal guard</h2>
                    <p className="small" style={{ marginTop: 0 }}>
                      <Pill tone={h.guard.enabled ? "ok" : "neutral"}>{h.guard.enabled ? "on" : "off"}</Pill>{" "}
                      Warns and records an event when the package stays at or above {h.guard.threshold_c} °C for {h.guard.hold_s} s. <span className="mono small weak">SHIMPAKU_THERMAL_GUARD=1</span>
                    </p>
                    <p className="small weak">It never shuts VMs down: which lab to stop, and whether losing a running exercise is worse than throttling, is the owner's choice.</p>
                    {h.guard.events.length ? (
                      <ul className="ops-events">{h.guard.events.map((e) => <li key={e.t}><span className="mono">{time(e.t)}</span> {e.temp_c} °C for {e.held_s} s</li>)}</ul>
                    ) : <p className="small weak">No guard events in the last 24 h.</p>}
                  </div>
                </div>
              </>
            );
          }}
        </Load>
      </div>
    </>
  );
}
