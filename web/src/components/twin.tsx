// The hardware twin: the KVM host and its seven vJunos guests as an isometric figure (drawn with
// primitives vendored from anatomy, MIT), and each guest as an original, generic front panel.
// Every LED, port and number comes from GET /api/hardware; nothing collected = shown as such.
import Info from "./Info";
import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import ZoomPan from "./ZoomPan";
import { usePoll } from "./poll";
import { fitProjection, insetPlan, iso, planOutline, sideMatrix, slabOf, topMatrix, type Plan, type Point3 } from "../vendor/anatomy/iso-kit";
import { Solid } from "../vendor/anatomy/Solid";
import { fmtBps } from "../live";
import "../vendor/anatomy/iso.css";
import "../hardware.css";

export type HwPort = {
  ifname: string; index: number; wired: boolean; peer: string | null; peer_if: string | null; role: string | null;
  link: string | null; link_state: string | null; lldp: string | null; oper: string | null; oper_source: string | null;
  admin: string | null; in_bps: number | null; out_bps: number | null; rate_t: number | null;
  counters: { input_packets: number | null; output_packets: number | null; input_errors: number | null; output_errors: number | null;
    input_drops: number | null; output_drops: number | null; carrier_transitions: number | null; mtu: string | null } | null;
  fixture?: { vlan: number; present: boolean | null };
};
export type HwDevice = {
  name: string; site: string | null; role: string | null; kind: string | null; model: string | null;
  state: "up" | "degraded" | "down" | "unknown"; reachable: boolean | null;
  cpu_pct: number | null; mem_pct: number | null; sampled_at: number | null;
  version: string | null; uptime_s: number | null; booted_at: number | null; facts_reason: string | null;
  declared: { vcpu: number | null; memory_mb: number | null } | null;
  ports: HwPort[]; counters_reason: string | null; incidents: string[];
};
export type HwModel = { image: string; equivalent: string; relation: string; facts: string[]; caveats: string[]; min: { vcpu: number; memory_mb: number }; sources: { label: string; url: string }[] };
export type Hardware = {
  generated_at: number; demo?: boolean;
  telemetry: { interval_s: number; transport: string; last_sample_t: number | null; rates: string | null; rates_source: string | null };
  hypervisor: {
    kind: string; hosts: number; devices: number; declared_vcpu: number | null; declared_memory_mb: number | null; declared_source: string | null;
    guests: Record<string, string>; guest_counts: Record<string, number>; guests_sampled_at: number | null; guests_reason: string | null;
    host: { t: number | null; ncpu: number | null; mem_total_mb: number | null; mem_avail_mb: number | null; load: number[] | null; temp_c: number | null; temp_crit_c: number | null } | null;
  };
  models: Record<string, HwModel>;
  devices: HwDevice[];
};

const ORDER = ["r1", "r2", "sw1", "spine1", "spine2", "leaf1", "leaf2"];
export const sortDevices = (d: HwDevice[]) => [...d].sort((a, b) => (ORDER.indexOf(a.name) + 99 * +(ORDER.indexOf(a.name) < 0)) - (ORDER.indexOf(b.name) + 99 * +(ORDER.indexOf(b.name) < 0)) || a.name.localeCompare(b.name));

/** A clock that ticks every `ms` (for "N s ago" timers). */
export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now() / 1000), ms); return () => window.clearInterval(id); }, [ms]);
  return now;
}

export function agoS(t: number | null | undefined, now: number) {
  if (!t) return "—";
  const s = Math.max(0, Math.round(now - t));
  if (s < 90) return `${s} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

export function uptime(s: number | null) {
  if (s == null) return null;
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

export const gib = (mb: number | null | undefined) => (mb == null ? "—" : `${Math.round((mb / 1024) * 10) / 10} GiB`);

/** Blink period for an activity LED: faster with more bits/s (log scale); null = no sample. */
export function blink(p: Pick<HwPort, "in_bps" | "out_bps">): { dur: string | null; idle: boolean; none: boolean } {
  if (p.in_bps == null && p.out_bps == null) return { dur: null, idle: false, none: true };
  const bps = (p.in_bps ?? 0) + (p.out_bps ?? 0);
  if (bps < 1) return { dur: null, idle: true, none: false };
  const s = Math.max(0.12, Math.min(1.6, 1.7 - 0.22 * Math.log10(bps)));
  return { dur: `${s.toFixed(2)}s`, idle: false, none: false };
}

export const operClass = (p: HwPort | undefined) =>
  !p || p.oper == null ? "unk" : p.admin === "down" ? "off" : p.oper === "up" ? (p.link_state && p.link_state !== "up" && p.wired ? "warn" : "up") : "down";

export const STATE_WORD: Record<string, string> = { up: "Up", degraded: "Degraded", down: "Down", unknown: "Unknown" };

export function portSummary(p: HwPort) {
  const parts = [`${p.ifname}`, p.oper ? `oper ${p.oper}` : "oper not reported"];
  if (p.peer) parts.push(`to ${p.peer} ${p.peer_if ?? ""}`.trim());
  if (p.fixture) parts.push(`demo trunk, VLAN ${p.fixture.vlan} ${p.fixture.present == null ? "unknown" : p.fixture.present ? "present" : "missing"}`);
  if (p.in_bps != null || p.out_bps != null) parts.push(`in ${fmtBps(p.in_bps)}, out ${fmtBps(p.out_bps)}`);
  return parts.join(", ");
}

/* ------------------------------------------------------------------------------------------------
 * The isometric figure: one host slab, seven guest slabs standing on it (campus, then fabric).
 * --------------------------------------------------------------------------------------------- */
// azimuth 68°: the row of guests runs almost left to right and their front panels face the viewer
const AZ = 68, EL = 30;
const ROW_GAP = 9, PITCH = 16, GW = 13, GD = 10, GH = 4.4, HOST_H = 5, LIFT = 1.4, SITE_GAP = 6, HOST_D = 17;

function useNarrow(q = "(max-width: 640px)") {
  const [m, setM] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => { const mq = window.matchMedia(q); const h = () => setM(mq.matches); mq.addEventListener("change", h); return () => mq.removeEventListener("change", h); }, [q]);
  return m;
}

export function HypervisorFigure({ hw, selected, onSelect, compact = false }: { hw: Hardware; selected?: string | null; onSelect?: (name: string) => void; compact?: boolean }) {
  const devs = useMemo(() => sortDevices(hw.devices), [hw.devices]);
  const narrow = useNarrow();
  // phones: two rows (campus behind, fabric in front) so each guest stays legible
  const rows = narrow ? 2 : 1;
  const slots = useMemo(() => {
    let x = 3, row = 0, prevSite: string | null = null;
    return devs.map((d) => {
      if (prevSite !== null && d.site !== prevSite) {
        if (rows === 2) { row = 1; x = 3; } else x += SITE_GAP;
      }
      prevSite = d.site;
      const s = { d, x, y: 3 + row * (GD + ROW_GAP) };
      x += PITCH;
      return s;
    });
  }, [devs, rows]);
  const hostW = Math.max(...slots.map((s) => s.x + GW), 0) + 3;
  const hostD = rows === 2 ? HOST_D + GD + ROW_GAP : HOST_D;
  const W = narrow ? 560 : 1000;
  const { P, H } = useMemo(() => {
    const pts: Point3[] = [[0, 0, 0], [hostW, 0, 0], [0, hostD, 0], [hostW, hostD, 0], [0, 0, HOST_H + LIFT + GH], [hostW, 0, HOST_H + LIFT + GH],
      [0, hostD, HOST_H], [hostW, hostD, HOST_H]];
    // the frame takes the figure's own aspect, so no empty band above or below it
    const pad = narrow ? 10 : 16;
    const u = fitProjection(pts, 1000, 1000, { pad: 0, azimuth: AZ, elevation: EL });
    const fl = pts.map((q) => iso(q, u));
    const aspect = (Math.max(...fl.map((f) => f[1])) - Math.min(...fl.map((f) => f[1]))) / (Math.max(...fl.map((f) => f[0])) - Math.min(...fl.map((f) => f[0])));
    const h = Math.round((W - 2 * pad) * aspect + 2 * pad);
    return { P: fitProjection(pts, W, h, { pad, azimuth: AZ, elevation: EL }), H: h };
  }, [hostW, hostD, W, narrow]);
  const host: Plan = { x: 0, y: 0, w: hostW, d: hostD, r: 1.6 };
  const hostSolid = slabOf(host, 0, HOST_H, P, 6, 0.5);
  const z0 = HOST_H + LIFT;
  const counts = hw.hypervisor.guest_counts;
  const running = counts.running ?? 0;
  const front = sideMatrix([0, hostD], HOST_H, P, "left");
  const sites = [...new Set(slots.map((s) => s.d.site))];
  const key = (n: string) => (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect?.(n); } };
  return (
    <svg className={`iso iso-svg twin-iso${compact ? " compact" : ""}`} viewBox={`0 0 ${W} ${H}`} role="group"
      aria-label={`One KVM host running ${devs.length} vJunos guests${Object.keys(hw.hypervisor.guests).length ? `, ${running} running` : ""}`}>
      <Solid paths={hostSolid} lit halo className="twin-host" tone="mid">
        <title>{`The lab host: one machine, libvirt / KVM, ${devs.length} vJunos guests`}</title>
      </Solid>
      {/* host front face: label, vents and one power LED per guest (virsh state) */}
      <g transform={front}>
        <text x={2.2} y={2.5} className="twin-host-t">KVM HOST · LIBVIRT</text>
        <text x={2.2} y={4.1} className="twin-host-s">1 machine · {devs.length} guests · {hw.hypervisor.declared_vcpu ?? "—"} vCPU · {gib(hw.hypervisor.declared_memory_mb)} declared</text>
        {Array.from({ length: Math.max(0, Math.floor((hostW - 70) / 1.6)) }).map((_, i) => (
          <line key={i} x1={hostW - 4 - i * 1.6} x2={hostW - 4 - i * 1.6} y1={1.4} y2={HOST_H - 1.4} className="twin-vent" />
        ))}
      </g>
      {/* guest footprints: where each VM "sits" on its host */}
      {slots.map(({ d, x, y }) => <path key={`f${d.name}`} d={planOutline(insetPlan({ x, y, w: GW, d: GD, r: 1 }, -0.4), HOST_H, P)} className="twin-slot" />)}
      {/* site labels on the host's front strip (fabric row labels sit in front of their row) */}
      {sites.map((site) => {
        const ss = slots.filter((s) => s.d.site === site);
        const x0 = ss[0].x, y0 = ss[0].y + GD + 1.2;
        return (
          <g key={`s${site}`} transform={topMatrix([x0, y0], HOST_H, P, "x")} aria-hidden>
            <text x={0} y={2.1} className="twin-site">{String(site ?? "").toUpperCase()}</text>
          </g>
        );
      })}
      {slots.map(({ d, x, y }) => {
        const plan: Plan = { x, y, w: GW, d: GD, r: 1 };
        const g = slabOf(plan, z0, GH, P, 6, 0.4);
        const top = topMatrix([x + 1.3, y + 1.2], z0 + GH, P, "x");
        const face = sideMatrix([x, y + GD], z0 + GH, P, "left");
        const sel = selected === d.name;
        const vm = hw.hypervisor.guests[d.name];
        const shown = d.ports.slice(0, 7);
        const rated = d.ports.filter((p) => p.in_bps != null || p.out_bps != null);
        const b = blink(rated.length ? { in_bps: rated.reduce((s, p) => s + (p.in_bps ?? 0), 0), out_bps: rated.reduce((s, p) => s + (p.out_bps ?? 0), 0) }
          : { in_bps: null, out_bps: null });
        const label = `${d.name}, ${d.role ?? ""} at ${d.site ?? ""}: ${STATE_WORD[d.state] ?? d.state}${vm ? `, VM ${vm}` : ""}${d.cpu_pct != null ? `, CPU ${d.cpu_pct}%` : ""}`;
        return (
          <g key={d.name} className={`twin-guest st-${d.state}${sel ? " sel" : ""}${onSelect ? " clickable" : ""}`}
            role={onSelect ? "button" : undefined} tabIndex={onSelect ? 0 : undefined} aria-label={label} aria-pressed={onSelect ? sel : undefined}
            onClick={onSelect ? () => onSelect(d.name) : undefined} onKeyDown={onSelect ? key(d.name) : undefined}>
            <title>{label}</title>
            <Solid paths={g} lit={sel} tone="hi" className="twin-guest-body" />
            <g transform={top} aria-hidden>
              <text x={0} y={2.9} className="twin-name">{d.name}</text>
              <text x={0} y={5.0} className="twin-role">{d.model === "vjunos-router" ? "vJunos-router" : d.model === "vjunos-switch" ? "vJunos-switch" : d.kind ?? "—"}</text>
              <rect x={0} y={6.1} width={GW - 2.6} height={0.9} rx={0.45} className="twin-bar-bg" />
              {d.cpu_pct != null && <rect x={0} y={6.1} width={Math.max(0.3, ((GW - 2.6) * Math.min(100, d.cpu_pct)) / 100)} height={0.9} rx={0.45} className={`twin-bar${d.cpu_pct >= 90 ? " hot" : ""}`} />}
              <text x={0} y={8.6} className="twin-cpu">{d.cpu_pct == null ? "CPU —" : `CPU ${Math.round(d.cpu_pct)}%`}{d.mem_pct != null ? ` · MEM ${Math.round(d.mem_pct)}%` : ""}</text>
            </g>
            <g transform={face} aria-hidden>
              <circle cx={1.1} cy={1.3} r={0.5} className={`twin-led st-${d.state}`} />
              {shown.map((p, i) => <rect key={p.ifname} x={2.4 + i * 1.35} y={0.8} width={1.0} height={1.1} rx={0.15} className={`twin-port-led o-${operClass(p)}`} />)}
              <circle cx={GW - 1.1} cy={1.3} r={0.5} className={`twin-act${b.none ? " none" : b.idle ? " idle" : ""}`} style={b.dur ? { animationDuration: b.dur } : undefined} />
              <text x={1.1} y={GH - 0.9} className="twin-face-t">{d.version ? `JUNOS ${d.version}` : "JUNOS —"}</text>
            </g>
          </g>
        );
      })}
    </svg>
  );
}

/* ------------------------------------------------------------------------------------------------
 * The front panel: an original, generic 1-FPC faceplate. Positions with no port reported are blanks.
 * --------------------------------------------------------------------------------------------- */
export function Faceplate({ d, vm, active, onHover, onPick }: { d: HwDevice; vm?: string; active?: string | null; onHover?: (ifname: string | null) => void; onPick?: (ifname: string) => void }) {
  const narrow = useNarrow();
  const byIdx = new Map(d.ports.filter((p) => /^ge-0\/0\/\d+$/.test(p.ifname)).map((p) => [p.index, p]));
  const other = d.ports.filter((p) => !/^ge-0\/0\/\d+$/.test(p.ifname));
  const maxIdx = Math.max(-1, ...byIdx.keys());
  const n = Math.max(8, maxIdx + 1);
  const PP = 38; // port pitch
  const portsW = n * PP;
  const left = narrow ? 18 : 150;
  const W = narrow ? Math.max(360, portsW + 36) : left + portsW + 196;
  const H = narrow ? 178 : 116;
  const py = narrow ? 92 : 40; // port row top
  const k = (fn: () => void) => (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); } };
  const kindWord = d.model === "vjunos-router" ? "vJunos-router" : d.model === "vjunos-switch" ? "vJunos-switch" : d.kind ?? "";
  return (
    <svg className="face" viewBox={`0 0 ${W} ${H}`} role="group" aria-label={`${d.name} front panel: ${d.ports.length} ports reported`}>
      <defs>
        <linearGradient id={`fg-${d.name}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#2a3833" /><stop offset="1" stopColor="#18221e" />
        </linearGradient>
      </defs>
      <rect x={1} y={4} width={W - 2} height={H - 8} rx={7} className="face-ear" />
      <rect x={14} y={8} width={W - 28} height={H - 16} rx={5} fill={`url(#fg-${d.name})`} className="face-body" />
      {[[7, 16], [7, H - 16], [W - 7, 16], [W - 7, H - 16]].map(([cx, cy], i) => <circle key={i} cx={cx} cy={cy} r={2.4} className="face-screw" />)}
      {/* identity block */}
      <g transform={narrow ? "translate(26 30)" : "translate(26 34)"}>
        <text className="face-name" x={0} y={0}>{d.name}</text>
        <text className="face-sub" x={0} y={15}>{kindWord}</text>
        {!narrow && <text className="face-sub" x={0} y={28}>1 RE · 1 FPC</text>}
        <g transform={narrow ? "translate(0 30)" : "translate(0 46)"}>
          <g><title>{`SYS: device state ${STATE_WORD[d.state] ?? d.state}`}</title><circle cx={4} cy={0} r={3.6} className={`face-led st-${d.state}`} /><text className="face-ledl" x={11} y={3.5}>SYS</text></g>
          <g><title>{`VM: ${vm ? `virsh says ${vm}` : "power state not sampled"}`}</title><circle cx={42} cy={0} r={3.6} className={`face-led vm-${vm ? (vm === "running" ? "up" : "off") : "unk"}`} /><text className="face-ledl" x={49} y={3.5}>VM</text></g>
          <g><title>{`NC: NETCONF ${d.reachable == null ? "not read" : d.reachable ? "answers" : "does not answer"}`}</title><circle cx={76} cy={0} r={3.6} className={`face-led ${d.reachable == null ? "vm-unk" : d.reachable ? "st-up" : "st-down"}`} /><text className="face-ledl" x={83} y={3.5}>NC</text></g>
        </g>
      </g>
      {narrow && (
        <g transform={`translate(${W - 26} 30)`} textAnchor="end">
          <text className="face-ver" x={0} y={0}>{d.version ? `JUNOS ${d.version}` : "JUNOS not collected"}</text>
          <text className="face-sub" x={0} y={15}>{uptime(d.uptime_s) ? `up ${uptime(d.uptime_s)}` : "uptime not collected"}</text>
        </g>
      )}
      {/* port block */}
      <g transform={`translate(${left} ${py})`}>
        <text className="face-blk" x={0} y={-8}>GE-0/0/x · FPC 0 · PIC 0</text>
        {Array.from({ length: n }).map((_, i) => {
          const p = byIdx.get(i);
          const x = i * PP;
          if (!p) {
            return (
              <g key={i} transform={`translate(${x} 0)`} className="face-blank">
                <title>{`ge-0/0/${i}: not reported by gNMI, NETCONF or the lab file (unused)`}</title>
                <rect x={2} y={4} width={PP - 8} height={24} rx={2.5} />
                <text x={(PP - 4) / 2} y={44} textAnchor="middle" className="face-idx dim">{i}</text>
              </g>
            );
          }
          const b = blink(p);
          const oc = operClass(p);
          const on = active === p.ifname;
          return (
            <g key={i} transform={`translate(${x} 0)`} className={`face-port o-${oc}${on ? " on" : ""}${p.fixture ? " fx" : ""}`} role="button" tabIndex={0}
              aria-label={portSummary(p)} aria-pressed={on}
              onMouseEnter={() => onHover?.(p.ifname)} onMouseLeave={() => onHover?.(null)} onFocus={() => onHover?.(p.ifname)} onBlur={() => onHover?.(null)}
              onClick={() => onPick?.(p.ifname)} onKeyDown={k(() => onPick?.(p.ifname))}>
              <title>{portSummary(p)}</title>
              <rect x={-1} y={-3} width={PP - 2} height={52} rx={4} className="face-hit" />
              <rect x={2} y={4} width={PP - 8} height={24} rx={2.5} className="face-jack" />
              <path d={`M${2 + (PP - 8) / 2 - 6} 28 v-5 h12 v5`} className="face-notch" />
              <rect x={5} y={7} width={6} height={3.2} rx={0.8} className={`face-link o-${oc}`} />
              <rect x={PP - 17} y={7} width={6} height={3.2} rx={0.8} className={`face-actl${b.none ? " none" : b.idle ? " idle" : ""}`} style={b.dur ? { animationDuration: b.dur } : undefined} />
              <text x={(PP - 4) / 2} y={44} textAnchor="middle" className="face-idx">{i}</text>
              {p.peer && <text x={(PP - 4) / 2} y={56} textAnchor="middle" className="face-peer">{p.peer}</text>}
              {p.fixture && <text x={(PP - 4) / 2} y={56} textAnchor="middle" className={`face-peer ${p.fixture.present === false ? "bad" : ""}`}>v{p.fixture.vlan}{p.fixture.present == null ? "?" : p.fixture.present ? "✓" : "✗"}</text>}
            </g>
          );
        })}
      </g>
      {!narrow && (
        <g transform={`translate(${W - 26} 36)`} textAnchor="end">
          <text className="face-ver" x={0} y={0}>{d.version ? `JUNOS ${d.version}` : "JUNOS not collected"}</text>
          <text className="face-sub" x={0} y={16}>{uptime(d.uptime_s) ? `up ${uptime(d.uptime_s)}` : "uptime not collected"}</text>
          <text className="face-sub" x={0} y={32}>{d.declared?.vcpu ?? "—"} vCPU · {gib(d.declared?.memory_mb)} (declared)</text>
          <text className="face-sub" x={0} y={48}>{other.length ? `+ ${other.map((p) => p.ifname).join(", ")}` : ""}</text>
        </g>
      )}
    </svg>
  );
}

const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString());

export function PortDetail({ p, d, now }: { p: HwPort | null | undefined; d: HwDevice; now: number }) {
  if (!p) {
    return (
      <div className="port-detail empty small weak">
        Hover or tap a port for its live counters.{d.counters_reason ? ` Counters: ${d.counters_reason}.` : ""}
      </div>
    );
  }
  const c = p.counters;
  const row = (k: string, v: ReactNode) => <><dt>{k}</dt><dd>{v}</dd></>;
  return (
    <div className="port-detail" aria-live="polite">
      <div className="pd-h">
        <b className="mono">{d.name} {p.ifname}</b>
        <span className={`pd-st o-${operClass(p)}`}>{p.oper ? `oper ${p.oper}` : "oper not reported"}{p.admin ? ` · admin ${p.admin}` : ""}</span>
        {p.peer && <span className="small">→ <b>{p.peer}</b> <span className="mono">{p.peer_if}</span> · {p.role}{p.link_state ? ` · link ${p.link_state}` : ""}</span>}
        {p.fixture && <span className={`small ${p.fixture.present === false ? "tone-crit" : ""}`}>demo trunk · VLAN {p.fixture.vlan} {p.fixture.present == null ? "unknown" : p.fixture.present ? "present" : "missing"}</span>}
      </div>
      <dl className="pd-grid">
        {row("In", fmtBps(p.in_bps))}
        {row("Out", fmtBps(p.out_bps))}
        {row("In / out packets", c ? `${num(c.input_packets)} / ${num(c.output_packets)}` : "—")}
        {row("Errors in / out", c ? `${num(c.input_errors)} / ${num(c.output_errors)}` : "—")}
        {row("Drops in / out", c ? `${num(c.input_drops)} / ${num(c.output_drops)}` : "—")}
        {row("Carrier transitions", c ? num(c.carrier_transitions) : "—")}
        {row("MTU", c?.mtu ?? "—")}
        {row("LLDP", p.lldp ?? "not reported")}
      </dl>
      <div className="small weak">
        Rates: gNMI counters, Δoctets × 8 / Δt{p.rate_t ? `, sampled ${agoS(p.rate_t, now)}` : ", no sample in the last 90 s"} · oper state: {p.oper_source ?? "no source"} · packets and errors: {c ? "live NETCONF read (≤ 30 s old)" : d.counters_reason ?? "not read"} · link speed is not collected, so no utilisation
      </div>
    </div>
  );
}

/** A generic, original chassis drawing for the real-world equivalent card (not a product image). */
export function ChassisSketch({ kind }: { kind: string }) {
  const router = kind === "vjunos-router";
  const plan: Plan = { x: 0, y: 0, w: router ? 22 : 18, d: 14, r: 0.8 };
  const h = router ? 16 : 26;
  const P = fitProjection([[0, 0, 0], [plan.w, 0, 0], [0, plan.d, 0], [plan.w, plan.d, 0], [0, 0, h], [plan.w, plan.d, h]], 200, 170, { pad: 10 });
  const body = slabOf(plan, 0, h, P, 4, 0.4);
  const face = sideMatrix([0, plan.d], h, P, "left");
  const slots = router ? 6 : 10;
  return (
    <svg className="iso iso-svg chassis-sketch" viewBox="0 0 200 170" role="img" aria-label="Illustration only: an original, generic chassis drawing, not a picture of any product">
      <Solid paths={body} lit halo tone="mid" />
      <g transform={face}>
        <rect x={1} y={1.2} width={plan.w - 2} height={router ? 2.6 : 3} rx={0.3} className="cs-band" />
        {Array.from({ length: slots }).map((_, i) => (
          <g key={i}>
            <rect x={1} y={(router ? 4.6 : 5) + i * (router ? 1.7 : 1.75)} width={plan.w - 2} height={router ? 1.3 : 1.35} rx={0.2} className="cs-slot" />
            {Array.from({ length: 8 }).map((_, j) => <rect key={j} x={plan.w - 2.2 - j * 1.1} y={(router ? 4.85 : 5.25) + i * (router ? 1.7 : 1.75)} width={0.7} height={0.75} className="cs-port" />)}
          </g>
        ))}
        <rect x={1} y={h - 3.4} width={plan.w - 2} height={2.4} rx={0.3} className="cs-band" />
      </g>
    </svg>
  );
}

export function LiveClock({ hw, now }: { hw: Hardware; now: number }) {
  const t = hw.telemetry.last_sample_t;
  const stale = !t || now - t > 60;
  return (
    <span className="small" aria-live="off">
      <span className={`live-dot${stale ? " stale" : ""}`} aria-hidden />
      {t ? <>Last gNMI sample <b>{agoS(t, now)}</b> · streamed every {hw.telemetry.interval_s} s · page read {agoS(hw.generated_at, now)}</>
        : "No telemetry sample stored yet"}
    </span>
  );
}


/** Overview: the hypervisor figure, compact, with the live clock; a guest opens its front panel. */
export function TwinStrip() {
  const q = usePoll<Hardware>("/api/hardware", 10000);
  const nav = useNavigate();
  const now = useNow(1000);
  const hw = q.data;
  if (!hw) return null; // the strip is an addition: on error or while loading the Overview reads as before
  const running = hw.hypervisor.guest_counts.running;
  return (
    <section className="card twin-strip" aria-labelledby="twin-h">
      <ZoomPan label="hypervisor figure"><HypervisorFigure hw={hw} compact onSelect={(n) => nav(`/hardware?d=${encodeURIComponent(n)}`)} /></ZoomPan>
      <div className="twin-strip-t">
        <div>
          <h2 id="twin-h">Seven real Junos devices, live<Info k="lab" /></h2>
          <p>
            {hw.devices.length} vJunos VMs on one {hw.hypervisor.kind} host{running != null ? `, ${running} running` : ""}: the Junos of
            EX9214-class switches and MX Series routers, streaming gNMI every {hw.telemetry.interval_s} s. The LEDs are their live port state.
          </p>
        </div>
        <div className="twin-strip-go">
          <LiveClock hw={hw} now={now} />
          <Link className="btn primary" to="/hardware">Open the hardware view →</Link>
        </div>
      </div>
    </section>
  );
}
