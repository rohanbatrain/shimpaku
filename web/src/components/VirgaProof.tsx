// "Proof from the real lab" beside a Virga drill: what the devices themselves report while the fault
// is in place, read from the same live sources as the rest of the dashboard and never from Virga's own
// runner. The front panels of the devices Virga touches (gNMI port state), the links on them as the
// topology sees them (with the moment each one changed, measured from the inject), the Junos events
// those devices log during the drill, and whether Shimpaku opened an incident on its own.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { usePoll } from "./poll";
import { useEventStream } from "../live";
import { Faceplate, type Hardware } from "./twin";
import { STATE_LABEL, STATE_TONE, type Topo } from "./TopoMap";
import { Pill } from "./ui";
import Info from "./Info";
import type { VTrial } from "../virga";

const hms = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const plus = (t: number, t0: number) => { const s = Math.round(t - t0); return s >= 0 ? `+${s} s` : `${s} s`; };

type Inc = { id: string; title: string; opened_at: number; status: string };

export default function VirgaProof({ t, live }: { t: VTrial; live: boolean }) {
  const nodes = useMemo(() => new Set(t.nodes ?? []), [t.nodes]);
  const inject = t.phases.find((p) => p.name === "inject")?.t_start ?? t.t_start;
  const end = live ? null : t.phases.reduce((m, p) => Math.max(m, p.t_end ?? 0), 0) || null;
  const hw = usePoll<Hardware>(nodes.size ? "/api/hardware" : null, 5000).data;
  const topo = usePoll<Topo>(nodes.size ? "/api/viz/topology" : null, 5000).data;
  const incs = usePoll<{ incidents: Inc[] }>("/api/incidents", 10000).data;
  const ev = useEventStream(200);

  // links touching the drill's devices, and the moment each one changed while this page watched
  const links = (topo?.links ?? []).filter((l) => nodes.has(l.a) || nodes.has(l.b));
  const seen = useRef(new Map<string, { state: string; at: number | null }>());
  const [, bump] = useState(0);
  useEffect(() => {
    let changed = false;
    for (const l of links) {
      const prev = seen.current.get(l.id);
      if (!prev) seen.current.set(l.id, { state: l.state, at: null });
      else if (prev.state !== l.state) { seen.current.set(l.id, { state: l.state, at: (topo?.generated_at ?? Date.now() / 1000) }); changed = true; }
    }
    if (changed) bump((n) => n + 1);
  }, [topo?.generated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  const events = ev.rows.filter((e) => nodes.has(e.device) && e.t != null && e.t >= t.t_start - 5 && (end == null || e.t <= end + 60))
    .sort((a, b) => (b.t ?? 0) - (a.t ?? 0)).slice(0, 10);
  const opened = (incs?.incidents ?? []).filter((i) => i.opened_at >= t.t_start - 5 && (end == null || i.opened_at <= end + 300))
    .sort((a, b) => a.opened_at - b.opened_at);
  const devs = (hw?.devices ?? []).filter((d) => nodes.has(d.name));

  return (
    <section className="vg-proof" aria-labelledby={`vg-proof-${t.id}`}>
      <div className="vg-proof-h">
        <h3 id={`vg-proof-${t.id}`}>Proof from the real lab<Info e={{ title: "Proof from the real lab", body: "Everything in this panel comes from the devices and Shimpaku's own monitoring, not from Virga: the port lights are the devices' live gNMI state, the links are what the topology reads, the log lines are what the Junos devices themselves recorded, and an incident appears only if Shimpaku noticed the fault on its own." }} /></h3>
        {live && <span className="vg-proof-live"><span className="live-dot" aria-hidden />watching live</span>}
      </div>

      <div className="vg-proof-grid">
        <div className="vg-proof-c">
          <div className="k">The devices, live<Info k="hypervisor" /></div>
          {devs.length === 0 && <div className="small weak">{nodes.size ? "Reading the devices…" : "This drill names no device."}</div>}
          {devs.map((d) => (
            <div key={d.name} className="vg-proof-dev">
              <div className="small"><b className="mono">{d.name}</b> <span className="weak">CPU {d.cpu_pct ?? "—"}% · {d.ports.filter((p) => p.wired && p.oper === "down").length} cabled port(s) down</span></div>
              <Faceplate d={d} vm={hw?.hypervisor.guests[d.name]} />
            </div>
          ))}
        </div>

        <div className="vg-proof-c">
          <div className="k">Links on these devices<Info k="topology" /></div>
          {links.length === 0 && <div className="small weak">{topo ? "No intended link touches these devices." : "Reading the topology…"}</div>}
          <ul className="vg-proof-list">
            {links.map((l) => {
              const at = seen.current.get(l.id)?.at;
              return (
                <li key={l.id} className={at ? "changed" : undefined}>
                  <Pill tone={STATE_TONE[l.state]}>{STATE_LABEL[l.state]}</Pill>
                  <span className="mono">{l.a} {l.a_if}</span> ↔ <span className="mono">{l.b} {l.b_if}</span>
                  {at && <span className="vg-proof-t">changed {hms(at)} · {plus(at, inject)} after inject</span>}
                  {l.bad.length > 0 && <div className="small tone-crit">{l.bad.slice(0, 2).join(" · ")}</div>}
                </li>
              );
            })}
          </ul>

          <div className="k" style={{ marginTop: 10 }}>Shimpaku's reaction<Info k="incident" /></div>
          {opened.length === 0
            ? <div className="small weak">{live ? "No incident yet. Shimpaku opens one when a service level stays below its goal." : "No incident was opened for this drill."}</div>
            : <ul className="vg-proof-list">{opened.map((i) => (
                <li key={i.id}><Pill tone="crit">Incident</Pill> <Link to={`/root-cause/${i.id}`} className="mono">{i.id}</Link> {i.title}
                  <span className="vg-proof-t">opened {hms(i.opened_at)} · {plus(i.opened_at, inject)} after inject</span></li>
              ))}</ul>}
        </div>

        <div className="vg-proof-c vg-proof-ev">
          <div className="k">What the devices logged<Info k="junos_events" /></div>
          {!ev.loaded && <div className="small weak">Connecting to the event stream…</div>}
          {ev.loaded && events.length === 0 && <div className="small weak">{live ? "Nothing logged by these devices yet. Port and routing changes show up here within seconds." : "No events from these devices during the drill."}</div>}
          <ol className="vg-proof-evl">
            {events.map((e) => (
              <li key={e.id} className={`sev-${e.severity}`}>
                <span className="mono vg-proof-t">{hms(e.t!)} · {plus(e.t!, inject)}</span>
                <span><b className="mono">{e.device}</b> <span className="mono weak">{e.type}</span> {e.message}</span>
              </li>
            ))}
          </ol>
          <Link className="small" to={`/events`}>All events →</Link>
        </div>
      </div>
    </section>
  );
}
