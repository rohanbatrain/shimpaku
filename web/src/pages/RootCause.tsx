import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Chat } from "grommet-icons";
import { useApi } from "../api";
import TopBar from "../components/TopBar";
import { FailBars } from "../components/charts";
import { Empty, Load, StatusPill } from "../components/ui";
import { ago, time } from "../format";
import { useSession } from "../session";
import BlastMap from "../components/BlastMap";

type Incident = { id: string; title: string; site: string; sle: string; classifier: string; status: string; opened_at: number; impact: Record<string, number>; action_id?: string | null; summary?: string };
type Node = { name: string; share_pct: number; children: Node[] };
type Blast = {
  tree: Node[]; timeline: { t: number; fail: number; total: number }[];
  affected: { kind: string; id: string; name: string; detail: string | null; impact_pct: number | null; failure_pct: number | null; minutes: number | null }[];
  distribution: { dimension: string; items: { name: string; failures: number; total: number }[] }[];
  evidence: { kind: string; title: string; detail: string; tool: string }[];
  scores?: { mutual_information?: number; lift?: number } | null;
  verdict?: string | null; scope?: unknown; n?: number; fails?: number;
};
const dash = (v: number | null | undefined, unit = "") => (v == null ? "—" : `${v}${unit}`);

function impactText(i: Record<string, number> | null | undefined) {
  if (!i || Object.keys(i).length === 0) return "—";
  if (i.clients != null) return `${i.clients} of ${i.of_clients} clients · ${i.probe_minutes} probe-min`;
  if (i.devices != null) return `${i.devices} of ${i.of_devices} devices · ${i.device_minutes} device-min`;
  return Object.entries(i).map(([k, v]) => `${k} ${v}`).join(" · ");
}

export function RootCauseIndex() {
  const q = useApi<{ incidents: Incident[] }>("/api/incidents", { url: "/api/stream/events", event: "incidents" });
  const nav = useNavigate();
  return (
    <>
      <TopBar title="Root cause" crumbs="Incidents grouped by service level and classifier" />
      <div className="content">
        <div className="card flush">
          <Load q={q} empty={(d) => (d.incidents.length ? null : <Empty title="No incidents">Every service level is meeting its goal.</Empty>)}>
            {(d) => (
              <table className="t">
                <thead><tr><th>Incident</th><th>Site</th><th>Service level · classifier</th><th>Impact</th><th>Opened</th><th>Status</th></tr></thead>
                <tbody>
                  {d.incidents.map((i) => (
                    <tr key={i.id} className="click" onClick={() => nav(`/root-cause/${i.id}`)}>
                      <td><Link to={`/root-cause/${i.id}`} onClick={(e) => e.stopPropagation()}><span className="mono">{i.id}</span></Link> · {i.title}</td>
                      <td>{i.site}</td>
                      <td>{i.sle} · <b>{i.classifier}</b></td>
                      <td className="small">{impactText(i.impact)}</td>
                      <td className="small weak">{ago(i.opened_at)}</td>
                      <td><StatusPill status={i.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Load>
        </div>
      </div>
    </>
  );
}

function Tree({ nodes, depth = 0 }: { nodes: Node[]; depth?: number }) {
  return (
    <>
      {nodes.map((n) => (
        <div key={n.name + depth}>
          <div className="cls" style={{ gridTemplateColumns: "210px 1fr 40px" }}>
            <span style={{ fontWeight: depth ? 400 : 600, paddingLeft: depth * 16 }}>{depth ? "└ " : ""}{n.name}</span>
            <span className={`bar${n.share_pct >= 50 ? " crit" : ""}`}><i style={{ width: `${n.share_pct}%` }} /></span>
            <span className="small" style={{ textAlign: "right" }}>{n.share_pct}%</span>
          </div>
          {n.children?.length > 0 && <Tree nodes={n.children} depth={depth + 1} />}
        </div>
      ))}
    </>
  );
}


/** The classifier tree as a horizontal node-link diagram: band widths are the real shares of bad units. */
function BlastTree({ b }: { b: Blast }) {
  const rows: { g: Node; c: Node; gi: number }[] = [];
  b.tree.forEach((g, gi) => (g.children?.length ? g.children : [{ name: "—", share_pct: g.share_pct, children: [] }]).forEach((c) => rows.push({ g, c, gi })));
  const RH = 52, top = 22, H = Math.max(rows.length * RH + top * 2, 140), W = 1180;
  const X0 = 170, X1 = 580, X2 = 900;
  const rootY = H / 2;
  const bw = (p: number) => Math.max(2, (p / 100) * 26);
  const gY = (gi: number) => { const r = rows.map((x, i) => [x, i] as const).filter(([x]) => x.gi === gi).map(([, i]) => i); return top + ((r[0] + r[r.length - 1]) / 2) * RH + RH / 2; };
  const curve = (x1: number, y1: number, x2: number, y2: number) => `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
  const tone = (p: number) => (p >= 50 ? "#D93A30" : p >= 20 ? "#D99A06" : "#7FBFA9");
  return (
    <svg className="blast-tree" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Blast radius: ${b.fails ?? "?"} failing of ${b.n ?? "?"} units, by classifier`}>
      {b.tree.map((g, gi) => <path key={"r" + gi} d={curve(X0 + 6, rootY, X1 - 8, gY(gi))} stroke={tone(g.share_pct)} strokeOpacity=".55" strokeWidth={bw(g.share_pct)} fill="none" />)}
      {rows.map((r, i) => <path key={"c" + i} d={curve(X1 + 8, gY(r.gi), X2 - 8, top + i * RH + RH / 2)} stroke={tone(r.c.share_pct)} strokeOpacity=".55" strokeWidth={bw(r.c.share_pct)} fill="none" />)}
      <g transform={`translate(${X0} ${rootY})`}>
        <rect x="-140" y="-26" width="146" height="52" rx="8" fill="#0E1A16" />
        <text x="-67" y="-5" textAnchor="middle" className="bt-root">{b.fails != null && b.n != null ? `${b.fails} of ${b.n}` : "Bad units"}</text>
        <text x="-67" y="13" textAnchor="middle" className="bt-root-s">{b.verdict ? `verdict: ${b.verdict}` : "failing units"}</text>
      </g>
      {b.tree.map((g, gi) => (
        <g key={"g" + gi} transform={`translate(${X1} ${gY(gi)})`}>
          <circle r="7" fill={tone(g.share_pct)} stroke="#fff" strokeWidth="2" />
          <text x="0" y="-24" textAnchor="middle" className="bt-l"><tspan fontWeight="600">{g.name}</tspan></text>
          <text x="0" y="32" textAnchor="middle" className="bt-v">{g.share_pct}%</text>
        </g>
      ))}
      {rows.map((r, i) => (
        <g key={"l" + i} transform={`translate(${X2} ${top + i * RH + RH / 2})`}>
          <circle r="5" fill={tone(r.c.share_pct)} stroke="#fff" strokeWidth="2" />
          <text x="14" y="4" className="bt-l">{r.c.name} <tspan className="bt-v" dx="6">{r.c.share_pct}%</tspan></text>
        </g>
      ))}
    </svg>
  );
}

export default function RootCause() {
  const { id = "" } = useParams();
  const inc = useApi<Incident>(`/api/incidents/${id}`);
  const br = useApi<Blast>(`/api/incidents/${id}/blast-radius`);
  const [tab, setTab] = useState<"affected" | "distribution" | "timeline">("affected");
  const { openAssistant } = useSession();

  return (
    <>
      <TopBar title={inc.data ? inc.data.title : "Root cause"} crumbs={<><Link to="/root-cause">Root cause</Link> / <span className="mono">{id}</span></>}>
        {inc.data && <StatusPill status={inc.data.status} />}
      </TopBar>
      <div className="content">
        <Load q={inc} rows={2}>
          {(i) => (
            <div className="card" style={{ display: "flex", gap: 28, alignItems: "flex-start", flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 420px" }}>
                <div className="k">Summary</div>
                <p style={{ margin: "4px 0 0" }}>{i.summary ?? "—"}</p>
              </div>
              <div><div className="k">Site</div><b>{i.site}</b></div>
              <div><div className="k">Service level</div><b>{i.sle}</b></div>
              <div><div className="k">Impact</div><b>{impactText(i.impact)}</b></div>
              <div><div className="k">Opened</div><b>{time(i.opened_at)}</b> <span className="small weak">({ago(i.opened_at)})</span></div>
              <div style={{ display: "flex", gap: 8 }}>
                {i.action_id && <Link className="btn primary" to={`/actions/${i.action_id}`}>Action {i.action_id} →</Link>}
                <button className="btn" onClick={() => openAssistant(`Explain incident ${i.id}`)}><Chat size="14px" color="#00785C" aria-hidden /> Ask Shim</button>
              </div>
            </div>
          )}
        </Load>
        <BlastMap id={id} />
        <Load q={br} rows={6}>
          {(b) => (
            <>
              {b.tree.length > 0 && (
                <div className="card">
                  <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                    <h2 style={{ margin: 0 }}>Blast radius</h2>
                    <span className="small weak">share of bad units per classifier → sub-classifier; band width = share</span>
                  </div>
                  <div className="blast-scroll"><BlastTree b={b} /></div>
                </div>
              )}
              <div className="grid2">
                <div className="card">
                  <h2>Classifier breakdown</h2>
                  {b.tree.length ? <Tree nodes={b.tree} /> : <span className="small weak">No classified failing units.</span>}
                  {b.verdict && (
                    <div className="small weak" style={{ marginTop: 10 }}>
                      Blast radius verdict: <b>{b.verdict}</b>{b.n != null && <> · {b.fails} failing of {b.n} units</>}
                    </div>
                  )}
                  {b.scores && (
                    <div className="small weak" style={{ marginTop: 10 }}>
                      Attribution: mutual information {b.scores.mutual_information ?? "—"} · lift {b.scores.lift ?? "—"}
                    </div>
                  )}
                </div>
                <div className="card">
                  <h2>Evidence</h2>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {b.evidence.length === 0 && <span className="small weak">No evidence recorded.</span>}
                    {b.evidence.map((e, j) => (
                      <div key={j} className="ev">
                        <b>{e.title}</b> <span className="chip" style={{ marginLeft: 6 }}>{e.tool}</span>
                        <div className="small weak">{e.detail}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <div className="card flush">
                <div className="tabs" role="tablist" aria-label="Blast radius views">
                  {(["affected", "distribution", "timeline"] as const).map((t) => (
                    <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
                      {t === "affected" ? `Affected items (${b.affected.length})` : t[0].toUpperCase() + t.slice(1)}
                    </button>
                  ))}
                </div>
                <div role="tabpanel">
                  {tab === "affected" && (
                    <table className="t">
                      <thead><tr><th>Item</th><th>Kind</th><th>Where</th><th className="num">Impact</th><th className="num">Failure rate</th><th className="num">Minutes</th></tr></thead>
                      <tbody>
                        {b.affected.map((a) => (
                          <tr key={a.kind + a.id}>
                            <td><b>{a.name}</b></td>
                            <td className="weak">{a.kind}</td>
                            <td className="mono">{a.detail ?? "—"}</td>
                            <td className="num">{dash(a.impact_pct, "%")}</td>
                            <td className="num">{dash(a.failure_pct, "%")}</td>
                            <td className="num">{dash(a.minutes)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {tab === "distribution" && (
                    <div className="grid2" style={{ padding: 16 }}>
                      {b.distribution.map((d) => (
                        <div key={d.dimension}>
                          <div className="k" style={{ marginBottom: 6 }}>{d.dimension}</div>
                          {d.items.map((it) => {
                            const p = it.total ? Math.round((it.failures / it.total) * 100) : 0;
                            return (
                              <div className="cls" key={it.name} style={{ gridTemplateColumns: "110px 1fr 90px" }}>
                                <span className="mono">{it.name}</span>
                                <span className={`bar${p >= 50 ? " crit" : ""}`}><i style={{ width: `${p}%` }} /></span>
                                <span className="small" style={{ textAlign: "right" }}>{it.failures}/{it.total} · {p}%</span>
                              </div>
                            );
                          })}
                        </div>
                      ))}
                    </div>
                  )}
                  {tab === "timeline" && (
                    <div style={{ padding: 16 }}>
                      <div className="small weak" style={{ marginBottom: 6 }}>
                        <span style={{ color: "#B3261E" }}>■</span> failing &nbsp; <span style={{ color: "#7fbfa9" }}>■</span> passing — per minute
                      </div>
                      {b.timeline.length ? <FailBars data={b.timeline} /> : <span className="small weak">Per-minute failure timeline not collected for this incident.</span>}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </Load>
      </div>
    </>
  );
}
