// The hardware twin page: seven real Junos devices, as VMs on one hypervisor, live.
import Info from "../components/Info";
import { useEffect, useState } from "react";
import ZoomPan from "../components/ZoomPan";
import { Link, useSearchParams } from "react-router";
import TopBar from "../components/TopBar";
import { usePoll } from "../components/poll";
import { Load, Pill } from "../components/ui";
import { STATE_TONE } from "../components/TopoMap";
import { agoS, ChassisSketch, Faceplate, gib, HypervisorFigure, LiveClock, PortDetail, sortDevices, STATE_WORD, uptime, useNow, type Hardware, type HwDevice } from "../components/twin";

function HypervisorCard({ hw, sel, onSelect, now }: { hw: Hardware; sel: string | null; onSelect: (n: string) => void; now: number }) {
  const hv = hw.hypervisor;
  const known = Object.keys(hv.guests).length;
  const running = hv.guest_counts.running ?? 0;
  const per = hw.devices.find((d) => d.declared)?.declared;
  const h = hv.host;
  return (
    <section className="card" aria-labelledby="hv-h">
      <div className="hv">
        <ZoomPan label="hypervisor figure"><HypervisorFigure hw={hw} selected={sel} onSelect={onSelect} /></ZoomPan>
        <div className="hv-text">
          <h2 id="hv-h">The hypervisor<Info k="hypervisor" /></h2>
          <p className="hv-lede">
            <b>Seven real Junos devices</b> run as virtual machines on <b>one {hv.kind} host</b>: the same Junos OS
            Juniper ships on EX9214-class switches and MX Series routers, on an x86 forwarding plane. Every LED and number
            here is read from them, live. Pick a guest to jump to its front panel.
          </p>
          <div>
            <dl className="hv-stats">
              <div><dt>Host</dt><dd>{hv.hosts} machine<small>{hv.kind}</small></dd></div>
              <div><dt>Guests</dt><dd>{known ? `${running} / ${hv.devices} running` : `${hv.devices} vJunos VMs`}
                <small>{known ? `virsh, ${agoS(hv.guests_sampled_at, now)}` : hv.guests_reason ?? "power state not sampled"}</small></dd></div>
              <div><dt>Declared</dt><dd>{hv.declared_vcpu ?? "—"} vCPU · {gib(hv.declared_memory_mb)}
                <small>{per ? `${per.vcpu} vCPU / ${gib(per.memory_mb)} each, Juniper's minimum` : "not declared"}</small></dd></div>
              <div><dt>Telemetry</dt><dd>gNMI every {hw.telemetry.interval_s} s<small>+ NETCONF reads, no SNMP</small></dd></div>
            </dl>
            {h && (
              <div className="hv-host">
                <b>Host, operator view:</b> {h.ncpu ?? "—"} CPU threads · {gib(h.mem_total_mb)} RAM ({gib(h.mem_avail_mb)} free)
                · load {h.load?.[0] ?? "—"} · CPU package {h.temp_c ?? "—"} °C{h.temp_crit_c ? ` of ${h.temp_crit_c} °C critical` : ""}
                {" "}<Link to="/ops/host">Lab host →</Link>
              </div>
            )}
            <div style={{ marginTop: 10 }}><LiveClock hw={hw} now={now} /></div>
          </div>
        </div>
      </div>
    </section>
  );
}

function Equivalents({ hw }: { hw: Hardware }) {
  const ms = Object.entries(hw.models);
  if (!ms.length) return null;
  return (
    <section aria-labelledby="eq-h">
      <h2 id="eq-h" style={{ margin: "4px 0 10px" }}>Real-world equivalent · what the same software runs on</h2>
      <div className="eq-grid">
        {ms.map(([k, m]) => {
          const n = hw.devices.filter((d) => d.model === k).map((d) => d.name);
          return (
            <article key={k} className="card eq">
              <figure style={{ margin: 0 }}>
                <ChassisSketch kind={k} />
                <figcaption>Original, generic drawing. Not a product image.</figcaption>
              </figure>
              <div>
                <div className="small weak">{m.image} · {n.join(", ")}</div>
                <h3>{m.equivalent}</h3>
                <p className="rel">{m.relation}</p>
                <ul>{m.facts.map((f) => <li key={f}>{f}</li>)}</ul>
                <ul className="cav">{m.caveats.map((f) => <li key={f}>{f}</li>)}</ul>
                <div className="src">{m.sources.map((s) => <a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.label} ↗</a>)}</div>
              </div>
            </article>
          );
        })}
      </div>
      <p className="small weak" style={{ margin: "8px 0 0" }}>
        Quotes are Juniper's documentation, retrieved 2026-10-25. Shimpaku is not affiliated with Juniper Networks or HPE;
        no logo, product photo or site design of theirs is used.
      </p>
    </section>
  );
}

export function DeviceCard({ d, hw, sel, now, page = true }: { d: HwDevice; hw: Hardware; sel: boolean; now: number; page?: boolean }) {
  const [hover, setHover] = useState<string | null>(null);
  const [pin, setPin] = useState<string | null>(null);
  const active = hover ?? pin;
  const port = d.ports.find((p) => p.ifname === active);
  const vm = hw.hypervisor.guests[d.name];
  return (
    <article id={`dev-${d.name}`} className={`card dev-card${sel ? " sel" : ""}`} aria-label={`${d.name} front panel`}>
      <div className="dev-h">
        <h2>{d.name}</h2>
        <Pill tone={STATE_TONE[d.state]}>{STATE_WORD[d.state] ?? d.state}</Pill>
        <span className="small weak">{d.role} · {d.site}</span>
        <span style={{ flex: 1 }} />
        {page ? <Link className="small" to={`/devices/${d.name}`}>Device page →</Link>
          : <Link className="small" to={`/hardware?d=${encodeURIComponent(d.name)}`}>On the hypervisor →</Link>}
      </div>
      <div className="dev-body">
        <Faceplate d={d} vm={vm} active={active} onHover={setHover} onPick={(i) => setPin((p) => (p === i ? null : i))} />
        <div className="dev-side">
          <div className="dev-stats">
            <span>CPU <b>{d.cpu_pct == null ? "—" : `${d.cpu_pct}%`}</b></span>
            <span>Memory <b>{d.mem_pct == null ? "—" : `${d.mem_pct}%`}</b></span>
            <span>{d.sampled_at ? `gNMI ${agoS(d.sampled_at, now)}` : "no CPU sample in 10 min"}</span>
            <span>Junos <b>{d.version ?? "not collected"}</b></span>
            <span>Uptime <b>{uptime(d.uptime_s) ?? "not collected"}</b></span>
            <span>VM <b>{vm ?? "—"}</b></span>
            <span>NETCONF <b>{d.reachable == null ? "—" : d.reachable ? "answers" : "no answer"}</b></span>
            {d.incidents.length > 0 && <span className="tone-crit">Open incident <Link to={`/root-cause/${d.incidents[0]}`}>{d.incidents[0]}</Link></span>}
          </div>
          <PortDetail p={port} d={d} now={now} />
        </div>
      </div>
    </article>
  );
}

export function TwinLegend() {
  return (
    <div className="twin-legend" aria-label="Legend">
      <span><i style={{ background: "var(--led-up)" }} />link LED: oper up</span>
      <span><i style={{ background: "var(--led-warn)" }} />up, link degraded</span>
      <span><i style={{ background: "var(--led-down)" }} />oper down</span>
      <span><i style={{ background: "var(--led-unk)" }} />not reported</span>
      <span><i style={{ background: "var(--led-act)" }} />activity: blinks faster with more bits/s (log scale)</span>
      <span>dashed position: no port reported there</span>
    </div>
  );
}

export default function HardwarePage() {
  const q = usePoll<Hardware>("/api/hardware", 5000);
  const [sp, setSp] = useSearchParams();
  const sel = sp.get("d");
  const now = useNow(1000);
  const select = (n: string) => {
    const next = new URLSearchParams(sp);
    if (n === sel) next.delete("d"); else next.set("d", n);
    setSp(next, { replace: true });
  };
  const loaded = !!q.data;
  useEffect(() => {
    if (!sel || !loaded) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById(`dev-${sel}`)?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, [sel, loaded]);
  return (
    <>
      <TopBar title="Hardware" crumbs="Seven vJunos devices on one hypervisor, live">
        {q.data && <LiveClock hw={q.data} now={now} />}
      </TopBar>
      <div className="content">
        <Load q={q} rows={6}>
          {(hw) => (
            <>
              <HypervisorCard hw={hw} sel={sel} onSelect={select} now={now} />
              <Equivalents hw={hw} />
              <section aria-labelledby="fp-h" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                  <h2 id="fp-h" style={{ margin: 0 }}>Front panels · live<Info k="vjunos" /></h2>
                  <span className="small weak">Each port as the device reports it; hover or tap one for its counters.</span>
                </div>
                <TwinLegend />
                {hw.telemetry.rates && <div className="small weak">Bit rates not available: {hw.telemetry.rates}</div>}
                <div className="dev-grid">
                  {sortDevices(hw.devices).map((d) => <DeviceCard key={d.name} d={d} hw={hw} sel={sel === d.name} now={now} />)}
                </div>
              </section>
            </>
          )}
        </Load>
      </div>
    </>
  );
}
