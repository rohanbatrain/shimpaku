import { useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router";
import { Close } from "grommet-icons";
import TopBar from "../components/TopBar";
import TopoMap, { Legend, LinkTable, STATE_LABEL, STATE_TONE, type Topo, type TNode } from "../components/TopoMap";
import { usePoll } from "../components/poll";
import { Load, Pill, StatusPill } from "../components/ui";
import { ago, time } from "../format";

const n0 = (v: number | null | undefined, unit = "") => (v == null ? "—" : `${Math.round(v * 10) / 10}${unit}`);

export function Freshness({ t }: { t?: number }) {
  return <span className="small weak" aria-live="off">{t ? `Updated ${time(t)} · refreshes every 10 s` : ""}</span>;
}

function NodePanel({ n, data, onClose }: { n: TNode; data: Topo; onClose: () => void }) {
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
      <Link className="btn primary" to={`/devices/${n.name}`} style={{ alignSelf: "flex-start" }}>Open device page →</Link>
    </aside>
  );
}

export default function Topology() {
  const q = usePoll<Topo>("/api/viz/topology");
  const [sp, setSp] = useSearchParams();
  const sel = sp.get("n");
  const select = (n: string | null) => setSp(n ? { n } : {}, { replace: true });
  return (
    <>
      <TopBar title="Topology" crumbs="Both sites, intended links with live state">
        <Freshness t={q.data?.generated_at} />
      </TopBar>
      <div className="content">
        <Load q={q} rows={8}>
          {(d) => {
            const node = d.nodes.find((n) => n.name === sel) ?? null;
            return (
              <>
                <div className={`topo-wrap${node ? " has-panel" : ""}`}>
                  <div className="card topo-card">
                    <TopoMap data={d} selected={sel} onSelect={(n) => select(n === sel ? null : n)} />
                    <Legend />
                    {Object.entries(d.sources).some(([, v]) => v !== "ok") && (
                      <div className="small weak" style={{ marginTop: 6 }}>
                        Sources: {Object.entries(d.sources).map(([k, v]) => `${k} ${v}`).join(" · ")}
                      </div>
                    )}
                  </div>
                  {node && <NodePanel n={node} data={d} onClose={() => select(null)} />}
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
