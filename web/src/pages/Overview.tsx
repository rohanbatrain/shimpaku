import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import TopBar from "../components/TopBar";
import TopoMap, { Legend, LinkTable, type Topo } from "../components/TopoMap";
import { usePoll } from "../components/poll";
import { Load, StatusPill } from "../components/ui";
import { statusLabel, STATUS_TONE, time } from "../format";
import { applyAsOf, Freshness } from "./Topology";
import EventTicker from "../components/EventTicker";
import { TwinStrip } from "../components/twin";
import Scrubber, { useTimeParam } from "../components/Scrubber";
import { useAsOf, useTraffic } from "../live";
import { FunnelSummary, type FunnelData } from "../components/Funnel";
import "../explain.css";

type Ov = {
  generated_at: number;
  devices: { total: number; reachable: number; known: number; degraded: number };
  links: { total: number; up: number; degraded: number; down: number; unknown: number };
  sles: { site: string; id: string; name: string; value_pct: number | null; n: number | null; good: number | null; bad: number | null; series: { t: number; value: number }[] }[];
  incidents: { open: number; total: number; available: boolean };
  actions_today: { since: number; total: number; by_status: Record<string, number>; blocked: number };
  audit: { ok: boolean; rows: number | null } | null;
  feed: { t: number; kind: string; text: string; status: string | null; ref: string | null; device?: string; site?: string }[];
};

const TONE_FILL: Record<string, string> = { ok: "#01A982", warn: "#D99A06", crit: "#D93A30", info: "#3A9CC0", neutral: "#A9B4AF" };

function Spark({ s }: { s: { t: number; value: number }[] }) {
  if (s.length < 2) return null;
  const w = 120, h = 28, t0 = s[0].t, t1 = s[s.length - 1].t || t0 + 1;
  const lo = Math.min(...s.map((p) => p.value), 90);
  const pts = s.map((p) => `${((p.t - t0) / (t1 - t0 || 1)) * w},${h - 2 - ((p.value - lo) / (100 - lo || 1)) * (h - 4)}`).join(" ");
  return <svg width={w} height={h} aria-hidden className="spark"><polyline points={pts} fill="none" stroke="#01A982" strokeWidth="2" strokeLinejoin="round" /></svg>;
}

function Tile({ k, children, foot, to }: { k: string; children: React.ReactNode; foot?: React.ReactNode; to?: string }) {
  const body = (
    <>
      <div className="k">{k}</div>
      <div className="kpi-v">{children}</div>
      {foot && <div className="small weak">{foot}</div>}
    </>
  );
  return to ? <Link className="card kpi link" to={to}>{body}</Link> : <div className="card kpi">{body}</div>;
}

function ActionsBar({ by }: { by: Record<string, number> }) {
  const groups: Record<string, number> = {};
  for (const [s, n] of Object.entries(by)) groups[statusLabel(s)] = (groups[statusLabel(s)] ?? 0) + n;
  const total = Object.values(groups).reduce((a, b) => a + b, 0);
  if (!total) return null;
  const items = Object.entries(groups).sort((a, b) => b[1] - a[1]);
  return (
    <>
      <div className="stack" aria-hidden>
        {items.map(([l, n]) => <i key={l} style={{ flex: n, background: TONE_FILL[STATUS_TONE[l] ?? "neutral"] }} />)}
      </div>
      <div className="stack-l">
        {items.map(([l, n]) => <span key={l}><i style={{ background: TONE_FILL[STATUS_TONE[l] ?? "neutral"] }} />{l} <b>{n}</b></span>)}
      </div>
    </>
  );
}

const FEED_KIND: Record<string, string> = { incident: "Incident", action: "Action", commit: "Commit" };

/** When the live feed runs much taller than the side-by-side map, the map stacks its sites and
 *  grows into the height beside the feed; otherwise it keeps the wide layout. */
function useFeedFit() {
  const map = useRef<HTMLDivElement>(null);
  const feed = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState<{ tall: boolean; h?: number }>({ tall: false });
  useEffect(() => {
    const m = map.current, f = feed.current;
    if (!m || !f || typeof ResizeObserver === "undefined") return;
    const check = () => {
      const side = window.matchMedia("(min-width: 1101px)").matches;  // below this the grid is one column
      const wideH = (m.clientWidth * 400) / 1040 + 110;
      const room = f.offsetHeight - 110;
      setFit(side && room > wideH + 120 ? { tall: true, h: Math.round(room) } : { tall: false });
    };
    const ro = new ResizeObserver(check);
    ro.observe(f); ro.observe(m);
    return () => ro.disconnect();
  }, []);
  return { map, feed, fit };
}

export default function Overview() {
  const { map: mapRef, feed: feedRef, fit } = useFeedFit();
  const ov = usePoll<Ov>("/api/viz/overview");
  const topo = usePoll<Topo>("/api/viz/topology");
  const nav = useNavigate();
  const [t, setT] = useTimeParam();
  const asOf = useAsOf(t);
  const traffic = useTraffic(t == null);
  const past = t != null ? asOf.data : undefined;
  const fun = usePoll<FunnelData>("/api/explain/funnel?window=7d", 30000);
  return (
    <>
      <TopBar title="Overview" crumbs="Seven vJunos devices, two sites, live">
        <Freshness t={ov.data?.generated_at} />
      </TopBar>
      <div className="content">
        <TwinStrip />
        <Load q={ov} rows={3}>
          {(o) => (
            <div className="kpis">
              <Tile k="Devices reachable" to="/topology" foot={o.devices.degraded ? `${o.devices.degraded} with a bad device-minute just now` : "live read of each device"}>
                <span className={o.devices.reachable === o.devices.total ? "tone-ok" : "tone-crit"}>{o.devices.reachable}</span>
                <span className="kpi-of">/ {o.devices.total}</span>
              </Tile>
              <Tile k="Links up" to="/topology" foot={[o.links.degraded && `${o.links.degraded} degraded`, o.links.down && `${o.links.down} down`, o.links.unknown && `${o.links.unknown} no evidence`].filter(Boolean).join(" · ") || "every intended link"}>
                {o.links.up}<span className="kpi-of">/ {o.links.total}</span>
              </Tile>
              <Tile k="Open incidents" to="/root-cause" foot={o.incidents.available ? `${o.incidents.total} recorded in total` : "not collected"}>
                <span className={o.incidents.open ? "tone-crit" : "tone-ok"}>{o.incidents.available ? o.incidents.open : "—"}</span>
              </Tile>
              <Tile k="Audit chain" to="/agents" foot={o.audit ? `hash chain recomputed over ${o.audit.rows ?? "—"} rows` : "audit log not available"}>
                {o.audit ? (o.audit.ok ? <span className="tone-ok">✓ Verified</span> : <span className="tone-crit">✗ Broken</span>) : "—"}
              </Tile>
              <div className="card kpi kpi-wide">
                <div className="k">Service levels · {t == null ? "last hour" : `hour ending ${time(t)}`}</div>
                {t != null && past?.available === false ? <span className="small weak">History not available: {past.reason}</span> :
                  (t != null ? past?.sles ?? [] : o.sles).length === 0 ? <span className="small weak">{t != null && asOf.loading ? "Reading stored minutes…" : "Not collected yet."}</span> : (
                  <div className="sle-mini">
                    {(t != null ? (past?.sles ?? []).map((s) => ({ ...s, series: [] as { t: number; value: number }[] })) : o.sles).map((s) => (
                      <Link key={s.site + s.id} to={`/sle?site=${s.site}`} className="sle-mini-i">
                        <span className="small weak">{s.site} · {s.name}</span>
                        <span><b className="kpi-s">{s.value_pct == null ? "—" : `${s.value_pct.toFixed(1)}%`}</b> <span className="small weak">n = {s.n ?? "—"}</span></span>
                        <Spark s={s.series} />
                      </Link>
                    ))}
                  </div>
                )}
                <div style={{ marginTop: 8 }}><Scrubber t={t} onChange={setT} label="Service level time travel" /></div>
              </div>
              <Link className="card kpi kpi-wide link" to="/actions">
                <div className="k">Actions today <span style={{ textTransform: "none", letterSpacing: 0 }}>(since {time(o.actions_today.since)})</span></div>
                <div className="kpi-v">{o.actions_today.total}{o.actions_today.blocked > 0 && <span className="kpi-of"> · {o.actions_today.blocked} blocked by guardrails</span>}</div>
                {o.actions_today.total ? <ActionsBar by={o.actions_today.by_status} /> : <span className="small weak">No actions proposed today.</span>}
              </Link>
            </div>
          )}
        </Load>
        <EventTicker />
        <div className="ov-grid">
          <div className="card" ref={mapRef}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6 }}>
              <h2 style={{ margin: 0 }}>Topology</h2>
              <span style={{ flex: 1 }} />
              <Link className="small" to="/topology">Open map →</Link>
            </div>
            <Load q={topo} rows={5}>
              {(d) => (
                <>
                  {t != null && <div className="small" style={{ color: "#8a5a00", marginBottom: 4 }}>As of {time(t)} · stored state</div>}
                  <TopoMap data={t == null ? d : applyAsOf(d, past)} compact layout={fit.tall ? "tall" : undefined} maxHeight={fit.h} onSelect={(n) => nav(`/topology?n=${n}${t != null ? `&t=${Math.floor(t)}` : ""}`)}
                    traffic={t == null ? (traffic?.available === false ? null : traffic?.links) : past?.traffic?.links} />
                  <Legend />
                  <div className="sr-only"><LinkTable data={d} /></div>
                </>
              )}
            </Load>
          </div>
          <div className="card" ref={feedRef}>
            <h2>Live feed</h2>
            <Load q={ov} rows={6}>
              {(o) => o.feed.length === 0 ? <span className="small weak">Nothing recorded yet.</span> : (
                <ul className="feed">
                  {o.feed.map((f, i) => (
                    <li key={i}>
                      <span className="mono weak">{time(f.t)}</span>
                      <span className={`feed-k k-${f.kind}`}>{FEED_KIND[f.kind] ?? f.kind}</span>
                      <span className="feed-t">
                        {f.ref ? <Link to={f.ref}>{f.text}</Link> : f.text}
                        {f.status && f.kind !== "commit" && <> <StatusPill status={f.status === "closed" ? "verified" : f.status} /></>}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Load>
          </div>
        </div>
        <div className="card ov-funnel">
          <div className="ex-head">
            <h2 style={{ margin: 0 }}>Guardrail funnel · last 7 days</h2>
            <span style={{ flex: 1 }} />
            <Link className="small" to="/guardrails">Open funnel →</Link>
          </div>
          <Load q={fun} rows={2}>{(f) => <FunnelSummary data={f} />}</Load>
        </div>
        <section className="card how" aria-labelledby="how-h">
          <h2 id="how-h">How this works</h2>
          <ol>
            <li><b>Collect</b><span>gNMI streams and Junos events from all seven devices into DuckDB.</span><Link to="/topology">Topology</Link></li>
            <li><b>Assure</b><span>Every minute is scored good or bad, attributed to one classifier, with n shown.</span><Link to="/sle">Service levels</Link></li>
            <li><b>Isolate</b><span>Incidents get a statistical blast radius and scored evidence.</span><Link to="/root-cause">Root cause</Link></li>
            <li><b>Propose</b><span>Shim, an MCP agent or a human proposes; nine guardrails run in code.</span><Link to="/actions">Actions</Link></li>
            <li><b>Approve &amp; validate</b><span>A human approves; <span className="mono">commit confirmed</span>, then a watched validation window.</span><Link to="/actions">Write path</Link></li>
          </ol>
        </section>
      </div>
    </>
  );
}
