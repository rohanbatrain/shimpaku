import { useState } from "react";
import { Link, useParams } from "react-router";
import TopBar from "../components/TopBar";
import { TrafficChart } from "../components/LazyCharts";
import { STATE_LABEL, STATE_TONE, type LinkState, type TLink, type Fixture } from "../components/TopoMap";
import { usePoll } from "../components/poll";
import { Load, Pill, Seg, StatusPill } from "../components/ui";
import { ago, time } from "../format";
import "../vizfix.css";

type Iface = { ifname: string; admin: string | null; oper: string | null; description: string | null; input_errors: number | null; input_drops: number | null; fcs_errors: number | null; output_errors: number | null; output_drops: number | null; carrier_transitions: number | null; input_packets: number | null; output_packets: number | null; mtu: string | null };
type Hist = { available?: false; reason?: string; step_s?: number; first_sample?: number; last_sample?: number; source?: string; interfaces?: { ifname: string; series: { t: number; in_bps?: number; out_bps?: number }[] }[] };
type Dev = {
  generated_at?: number;
  device: { name: string; site: string; kind: string | null; role: string; version: string | null; topology: string | null; state: LinkState; reachable: boolean | null };
  window_min: number;
  health: { available?: false; reason?: string; cpu_pct: number | null; mem_pct: number | null; sampled_at: number | null; minutes: { n: number; good: number; bad: number }; classifiers: { classifier: string; sub_classifier: string | null; minutes: number }[]; last: { status: string; minute: number } | null };
  interfaces: Iface[] | null; interfaces_reason: string | null; history: Hist;
  routing: { available?: false; reason?: string; ospf?: { interface: string; state: string; address: string; neighbor_id: string }[]; bfd?: { interface: string; state: string; neighbor: string }[]; bgp?: { peer: string; state: string; peer_as: string; flaps: number }[] };
  links: TLink[]; incidents: string[]; actions: { id: string; status: string; title: string; created_at: number }[]; fixture: Fixture | null;
};

const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString());
function peer(links: TLink[], me: string, ifn: string) {
  const l = links.find((l) => (l.a === me && l.a_if === ifn) || (l.b === me && l.b_if === ifn));
  return l ? `→ ${l.a === me ? l.b : l.a}` : "";
}
const W = [{ value: "60", label: "1 h" }, { value: "360", label: "6 h" }, { value: "1440", label: "24 h" }];

export default function Device() {
  const { name = "" } = useParams();
  const [win, setWin] = useState("60");
  const q = usePoll<Dev>(`/api/viz/devices/${encodeURIComponent(name)}?window=${win}`, 15000);
  const d = q.data;
  return (
    <>
      <TopBar title={name} crumbs={<><Link to="/topology">Topology</Link> / device</>}>
        {d && <Pill tone={STATE_TONE[d.device.state]}>{STATE_LABEL[d.device.state]}</Pill>}
        <Link className="btn" to={`/hardware?d=${encodeURIComponent(name)}`}>Front panel →</Link>
        <Seg label="History window" value={win} options={W} onChange={setWin} />
      </TopBar>
      <div className="content">
        <Load q={q} rows={8}>
          {(d) => {
            const h = d.health;
            return (
              <>
                <div className="kpis">
                  <div className="card kpi"><div className="k">Role · site</div><div className="kpi-v kpi-s">{d.device.role} · {d.device.site}</div><div className="small weak mono">{d.device.kind ?? "—"} · Junos version {d.device.version ?? "not collected"}</div></div>
                  <div className="card kpi"><div className="k">CPU / memory</div><div className="kpi-v">{h.available === false || h.cpu_pct == null ? "—" : `${h.cpu_pct}%`}<span className="kpi-of"> / {h.mem_pct == null ? "—" : `${h.mem_pct}%`}</span></div><div className="small weak">{h.sampled_at ? `sampled ${ago(h.sampled_at)} (gNMI)` : "no sample in the last 10 min"}</div></div>
                  <div className="card kpi"><div className="k">Device-minutes · {d.window_min >= 60 ? `${d.window_min / 60} h` : `${d.window_min} min`}</div><div className="kpi-v">{h.available === false || !h.minutes.n ? "—" : <><span className={h.minutes.bad ? "tone-warn" : "tone-ok"}>{h.minutes.good}</span><span className="kpi-of"> good / n = {h.minutes.n}</span></>}</div><div className="small weak">{h.classifiers?.length ? h.classifiers.map((c) => `${c.classifier}${c.sub_classifier ? " · " + c.sub_classifier : ""} ${c.minutes} min`).join(", ") : "no bad minutes"}</div></div>
                  <div className="card kpi"><div className="k">Reachable</div><div className="kpi-v">{d.device.reachable == null ? "—" : d.device.reachable ? <span className="tone-ok">Yes</span> : <span className="tone-crit">No</span>}</div><div className="small weak">live NETCONF read</div></div>
                </div>
                {d.fixture && (
                  <div className={`notice ${d.fixture.present == null ? "info" : d.fixture.present ? "ok" : "crit"}`}>
                    <b>Demo trunk {d.fixture.ifname}</b> (wired to nothing by design, so oper {d.fixture.oper ?? "—"}): VLAN {d.fixture.vlan} is{" "}
                    <b>{d.fixture.present == null ? "unknown" : d.fixture.present ? "present" : "missing"}</b>; VLAN {d.fixture.keep_vlan} stays as the keepalive member.
                  </div>
                )}
                <div className="card">
                  <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                    <h2 style={{ margin: 0 }}>Traffic</h2>
                    <span className="small weak">
                      {d.history.available === false ? "" : `${d.history.source}; rate per ${(d.history.step_s ?? 60) / 60} min between consecutive counter samples · data since ${time(d.history.first_sample)}`}
                    </span>
                  </div>
                  {d.history.available === false || !d.history.interfaces?.length ? (
                    <div className="state"><b style={{ color: "var(--t)" }}>History not collected</b><span className="small">{d.history.reason ?? "No stored interface counters for this window."} Current counters are below.</span></div>
                  ) : (
                    <div className="grid2" style={{ marginTop: 10 }}>
                      {d.history.interfaces.map((i) => (
                        <div key={i.ifname}>
                          <div className="mono" style={{ marginBottom: 2 }}>{i.ifname} <span className="weak small">{peer(d.links, name, i.ifname)}</span></div>
                          <TrafficChart series={i.series} label={`${i.ifname} traffic, in and out bits per second`} />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="card flush">
                  <div className="card-h"><h2>Interfaces</h2><span className="small weak">live counters since the last clear (show interfaces extensive)</span></div>
                  {d.interfaces == null ? <div className="state small">{d.interfaces_reason ?? "Not read."}</div> : (
                    <table className="t ifcards">
                      <thead><tr><th>Port</th><th>Admin / oper</th><th className="num">In pkts</th><th className="num">Out pkts</th><th className="num">In errors</th><th className="num">In drops</th><th className="num">Out errors</th><th className="num">Out drops</th><th className="num">FCS</th><th className="num">Carrier transitions</th></tr></thead>
                      <tbody>
                        {d.interfaces.map((i) => (
                          <tr key={i.ifname}>
                            <td className="mono if-name">{i.ifname}{i.description ? <span className="weak"> · {i.description}</span> : null}</td>
                            <td data-label="Admin / oper"><Pill tone={i.oper === "up" ? "ok" : i.admin === "up" ? "crit" : "neutral"}>{i.admin ?? "—"} / {i.oper ?? "—"}</Pill></td>
                            <td className="num" data-label="In pkts">{num(i.input_packets)}</td><td className="num" data-label="Out pkts">{num(i.output_packets)}</td>
                            <td className="num" data-label="In errors">{num(i.input_errors)}</td><td className="num" data-label="In drops">{num(i.input_drops)}</td>
                            <td className="num" data-label="Out errors">{num(i.output_errors)}</td><td className="num" data-label="Out drops">{num(i.output_drops)}</td>
                            <td className="num" data-label="FCS" title={i.fcs_errors == null ? "not reported on virtio ports" : undefined}>{num(i.fcs_errors)}</td>
                            <td className="num" data-label="Carrier transitions">{num(i.carrier_transitions)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
                <div className="grid2">
                  <div className="card">
                    <h2>Routing adjacencies</h2>
                    {d.routing.available === false ? <span className="small weak">{d.routing.reason}</span> : (
                      <ul className="plain">
                        {(d.routing.ospf ?? []).map((o, i) => <li key={"o" + i}><Pill tone={o.state === "Full" ? "ok" : "warn"}>OSPF {o.state}</Pill> <span className="mono">{o.interface}</span> <span className="weak small">router-id {o.neighbor_id}</span></li>)}
                        {(d.routing.bfd ?? []).map((o, i) => <li key={"b" + i}><Pill tone={o.state === "Up" ? "ok" : "crit"}>BFD {o.state}</Pill> <span className="mono">{o.interface}</span></li>)}
                        {(d.routing.bgp ?? []).map((o, i) => <li key={"g" + i}><Pill tone={o.state === "Established" ? "ok" : "crit"}>BGP {o.state}</Pill> AS {o.peer_as} <span className="weak small">· {o.flaps} flaps</span></li>)}
                        {!(d.routing.ospf?.length || d.routing.bfd?.length || d.routing.bgp?.length) && <li className="small weak">No OSPF, BFD or BGP sessions on this device (expected for an L2 switch).</li>}
                      </ul>
                    )}
                  </div>
                  <div className="card">
                    <h2>Links, incidents, actions</h2>
                    <ul className="plain">
                      {d.links.map((l) => <li key={l.id}><Pill tone={STATE_TONE[l.state]}>{STATE_LABEL[l.state]}</Pill> <span className="mono">{l.a} {l.a_if} ↔ {l.b} {l.b_if}</span>{l.bad.length > 0 && <div className="small tone-crit">{l.bad.join(" · ")}</div>}</li>)}
                      {d.incidents.map((i) => <li key={i}>Open incident <Link className="mono" to={`/root-cause/${i}`}>{i}</Link></li>)}
                      {d.actions.map((a) => <li key={a.id}><StatusPill status={a.status} /> <Link to={`/actions/${a.id}`}>{a.title}</Link> <span className="small weak">{ago(a.created_at)}</span></li>)}
                      {!d.incidents.length && !d.actions.length && <li className="small weak">No open incidents or actions in the last 24 h.</li>}
                    </ul>
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
