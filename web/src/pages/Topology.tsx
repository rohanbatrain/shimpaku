import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { Close } from "grommet-icons";
import TopBar from "../components/TopBar";
import TopoMap, { Legend, LinkTable, STATE_LABEL, STATE_TONE, type Topo, type TNode } from "../components/TopoMap";
import { usePoll } from "../components/poll";
import { Load, Pill, Seg, StatusPill } from "../components/ui";
import { ago, time } from "../format";
import Scrubber, { useTimeParam } from "../components/Scrubber";
import { fmtBps, useAsOf, useTraffic, type AsOf, type TrafficLink } from "../live";

const Topo3D = lazy(() => import("../components/Topo3D"));

/** The 3D view is optional: if it fails to load or draw, the 2D map shows instead. */
class Fallback extends Component<{ to: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.to : this.props.children; }
}

const n0 = (v: number | null | undefined, unit = "") => (v == null ? "—" : `${Math.round(v * 10) / 10}${unit}`);

export function Freshness({ t }: { t?: number }) {
  return <span className="small weak" aria-live="off">{t ? `Updated ${time(t)} · refreshes every 10 s` : ""}</span>;
}

/** The live map data with link / node state replaced by the stored state as of a past minute. */
export function applyAsOf(d: Topo, a: AsOf | undefined): Topo {
  if (!a || a.available === false) return d;
  const L = new Map(a.links.map((l) => [l.id, l]));
  const N = new Map(a.nodes.map((n) => [n.name, n]));
  return {
    ...d,
    links: d.links.map((l) => { const x = L.get(l.id); return x ? { ...l, state: x.state, good: x.good, bad: x.bad, evidence: x.evidence } : { ...l, state: "unknown", good: [], bad: [] }; }),
    nodes: d.nodes.map((n) => { const x = N.get(n.name); return { ...n, state: x ? x.state : "unknown" }; }),
  };
}

export function AsOfBanner({ t, a, onLive }: { t: number; a?: AsOf; onLive: () => void }) {
  return (
    <div className="asof-banner" role="status">
      <span>Showing stored state <b>as of {time(t)}</b>{a?.available === false ? ` — ${a.reason ?? "history not available"}` : ""}</span>
      <span className="small">{a?.available !== false ? "links: port oper status + OSPF; devices: collector connection + device-minute verdict" : ""}</span>
      <span style={{ flex: 1 }} />
      <button className="btn" onClick={onLive}>Back to live</button>
    </div>
  );
}

export function TrafficLegend({ live, speedReason, at }: { live: boolean; speedReason?: string | null; at?: number | null }) {
  return (
    <div className="traffic-bar">
      <span className="sw"><svg width="34" height="10" aria-hidden><line x1="2" y1="5" x2="32" y2="5" stroke="#2F6690" strokeWidth="4" strokeDasharray="7 9" strokeLinecap="round" /></svg>
        Traffic: width = bits/s (log scale), dashes move with the flow, one lane per direction</span>
      <span>{speedReason ? "Link speed unknown — absolute rates only, no utilisation colour" : ""}</span>
      <span className="weak">{at ? `${live ? "counters sampled" : "as of"} ${time(at)}` : "no counter samples"}</span>
    </div>
  );
}

export function TrafficPop({ t, onClose }: { t: TrafficLink; onClose: () => void }) {
  return (
    <div className="traffic-pop" role="region" aria-label={`Traffic on ${t.a}–${t.b}`}>
      <span className="dir">{t.a} {t.a_if} → {t.b}</span><span className="v">{fmtBps(t.ab_bps)}</span>
      <span className="dir">{t.b} {t.b_if} → {t.a}</span><span className="v">{fmtBps(t.ba_bps)}</span>
      <span className="foot">
        {t.reason ?? (t.sample_t ? `Sample at ${new Date(t.sample_t * 1000).toLocaleTimeString([], { hour12: false })}${t.stale ? " — stale (over 60 s old)" : ""}` : "")}
        {" · "}{t.speed_bps ? `link speed ${fmtBps(t.speed_bps)}` : "link speed not collected, utilisation unknown"}
      </span>
      <button className="btn" onClick={onClose}>Close</button>
    </div>
  );
}

function NodePanel({ n, data, onClose, past }: { n: TNode; data: Topo; onClose: () => void; past?: boolean }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => { ref.current?.focus(); }, [n.name]);
  const links = data.links.filter((l) => l.a === n.name || l.b === n.name);
  const h = n.health;
  const fx = data.fixture.device === n.name ? data.fixture : null;
  return (
    <aside ref={ref} tabIndex={-1} className="node-panel card" aria-label={`${n.name} details`}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h2 style={{ margin: 0, fontSize: 18, textTransform: "none", letterSpacing: 0, color: "var(--t)" }}>{n.name}</h2>
        <Pill tone={STATE_TONE[n.state]}>{STATE_LABEL[n.state]}</Pill>
        <span style={{ flex: 1 }} />
        <button className="icon-btn" onClick={onClose} aria-label="Close details"><Close size="16px" color="currentColor" /></button>
      </div>
      {past && <div className="notice info small">These details are live (now), not as of the chosen minute.</div>}
      <dl className="kv kv-narrow">
        <dt>Role</dt><dd>{n.role} · {n.site}</dd>
        <dt>Model</dt><dd className="mono">{n.kind ?? "—"}</dd>
        <dt>Junos version</dt><dd>{n.version ?? <span className="weak">not collected</span>}</dd>
        <dt>Reachable</dt><dd>{n.reachable == null ? "—" : n.reachable ? "yes (live read)" : "no — live read failed"}</dd>
        <dt>CPU / memory</dt><dd>{h ? `${n0(h.cpu_pct, "%")} / ${n0(h.mem_pct, "%")}` : "—"}{h?.sampled_at ? <span className="weak small"> · {ago(h.sampled_at)}</span> : null}</dd>
        <dt>Device-minutes, 15 min</dt><dd>{h && h.minutes.n ? <>{h.minutes.good} good of {h.minutes.n}{h.minutes.bad ? <span className="tone-crit"> · {h.minutes.bad} bad</span> : null}</> : <span className="weak">none stored</span>}</dd>
        {h?.last?.status === "bad" && <><dt>Last verdict</dt><dd className="tone-crit">{[h.last.classifier, h.last.sub_classifier].filter(Boolean).join(" · ")}</dd></>}
        <dt>Routing</dt><dd>{n.routing ? <>OSPF {n.routing.ospf_full}/{n.routing.ospf} Full · BFD {n.routing.bfd_up}/{n.routing.bfd} Up{n.routing.bgp ? <> · BGP {n.routing.bgp_established}/{n.routing.bgp}</> : null}</> : "—"}</dd>
      </dl>
      {fx && (
        <div className={`notice ${fx.present == null ? "info" : fx.present ? "ok" : "crit"}`}>
          <b>Demo trunk {fx.ifname}:</b> VLAN {fx.vlan} {fx.present == null ? "state unknown" : fx.present ? "present" : "missing — the injected fault"}
          {fx.present === false && <> · <Link to="/actions">fix it through Actions →</Link></>}
        </div>
      )}
      <div>
        <div className="k" style={{ marginBottom: 6 }}>Links</div>
        {links.length === 0 && <span className="small weak">No intended links.</span>}
        <ul className="plain">
          {links.map((l) => {
            const me = l.a === n.name ? [l.a_if, l.b, l.b_if] : [l.b_if, l.a, l.a_if];
            return (
              <li key={l.id}>
                <Pill tone={STATE_TONE[l.state]}>{STATE_LABEL[l.state]}</Pill>{" "}
                <span className="mono">{me[0]}</span> → <b>{me[1]}</b> <span className="mono weak">{me[2]}</span>
                {l.bad.length > 0 && <div className="small tone-crit">{l.bad.join(" · ")}</div>}
              </li>
            );
          })}
        </ul>
      </div>
      <div>
        <div className="k" style={{ marginBottom: 6 }}>Open incidents</div>
        {n.incidents.length === 0 ? <span className="small weak">None touch {n.name}.</span> : n.incidents.map((i) => <div key={i}><Link className="mono" to={`/root-cause/${i}`}>{i}</Link></div>)}
      </div>
      <div>
        <div className="k" style={{ marginBottom: 6 }}>Actions, last 24 h</div>
        {n.actions.length === 0 ? <span className="small weak">None on {n.name}.</span> : (
          <ul className="plain">
            {n.actions.map((a) => <li key={a.id}><StatusPill status={a.status} /> <Link to={`/actions/${a.id}`}>{a.title}</Link> <span className="small weak">{ago(a.created_at)}</span></li>)}
          </ul>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Link className="btn primary" to={`/devices/${n.name}`}>Open device page →</Link>
        <Link className="btn" to={`/hardware?d=${encodeURIComponent(n.name)}`}>Front panel →</Link>
      </div>
    </aside>
  );
}

export default function Topology() {
  const q = usePoll<Topo>("/api/viz/topology");
  const [sp, setSp] = useSearchParams();
  const sel = sp.get("n");
  const view = sp.get("view") === "3d" ? "3d" : "2d";
  const setView = (v: "2d" | "3d") => {
    const next = new URLSearchParams(sp);
    if (v === "3d") next.set("view", "3d"); else next.delete("view");
    setSp(next, { replace: true });
  };
  const [t, setT] = useTimeParam();
  const live = useTraffic(t == null);
  const asOf = useAsOf(t);
  const [selLink, setSelLink] = useState<string | null>(null);
  const select = (n: string | null) => {
    const next = new URLSearchParams(sp);
    if (n) next.set("n", n); else next.delete("n");
    setSp(next, { replace: true });
  };
  const traffic = t == null ? (live?.available === false ? null : live?.links) : asOf.data?.traffic?.links;
  const trafficAt = t == null ? live?.at : asOf.data?.traffic?.at;
  return (
    <>
      <TopBar title="Topology" crumbs="Both sites, intended links with live state">
        <Freshness t={q.data?.generated_at} />
        <Seg label="Map view" value={view} options={[{ value: "2d", label: "2D" }, { value: "3d", label: "3D" }]} onChange={setView} />
      </TopBar>
      <div className="content">
        <div className="card"><Scrubber t={t} onChange={setT} label="Topology time travel" /></div>
        {t != null && <AsOfBanner t={t} a={asOf.data} onLive={() => setT(null)} />}
        <Load q={q} rows={8}>
          {(d0) => {
            const d = t == null ? d0 : applyAsOf(d0, asOf.data);
            const node = d0.nodes.find((n) => n.name === sel) ?? null;
            const tl = traffic?.find((x) => x.id === selLink);
            return (
              <>
                <div className={`topo-wrap${node ? " has-panel" : ""}`}>
                  <div className="card topo-card">
                    {(() => {
                      const flat = <TopoMap data={d} selected={sel} onSelect={(n) => select(n === sel ? null : n)}
                        traffic={traffic} onLink={(id) => setSelLink(id === selLink ? null : id)} selectedLink={selLink} />;
                      return view === "3d"
                        ? <Fallback to={flat}><Suspense fallback={flat}><Topo3D data={d} selected={sel} onSelect={(n) => select(n === sel ? null : n)} /></Suspense></Fallback>
                        : flat;
                    })()}
                    <Legend />
                    <TrafficLegend live={t == null} speedReason={t == null ? live?.speed_reason : "not collected"} at={trafficAt} />
                    {t == null && live?.available === false && <div className="small weak">Traffic not available: {live.reason}</div>}
                    {tl && <TrafficPop t={tl} onClose={() => setSelLink(null)} />}
                    {Object.entries(d.sources).some(([, v]) => v !== "ok") && (
                      <div className="small weak" style={{ marginTop: 6 }}>
                        Sources: {Object.entries(d.sources).map(([k, v]) => `${k} ${v}`).join(" · ")}
                      </div>
                    )}
                  </div>
                  {node && <NodePanel n={node} data={d0} past={t != null} onClose={() => select(null)} />}
                </div>
                <div className="card flush">
                  <div className="card-h"><h2>Links</h2><span className="small weak">the same data as the map</span></div>
                  <LinkTable data={d} />
                </div>
              </>
            );
          }}
        </Load>
      </div>
    </>
  );
}
