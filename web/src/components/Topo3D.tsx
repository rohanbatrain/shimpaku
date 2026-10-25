// The topology in 3D: each device is the same vJunos chassis the hardware twin draws, standing at its
// place on the map, on one pad per site, with the intended links on the floor in their live state.
// An optional view: Topology loads it lazily and falls back to the 2D map if it fails.
import { useMemo, type KeyboardEvent } from "react";
import { usePoll } from "./poll";
import { depthOf, fitProjection, iso, onTop, sideMatrix, slabOf, topMatrix, type Plan, type Point3 } from "../vendor/anatomy/iso-kit";
import { Solid } from "../vendor/anatomy/Solid";
import { POS_WIDE, SITES_WIDE, STATE_COLOR, type Topo } from "./TopoMap";
import { blink, operClass, STATE_WORD, type Hardware } from "./twin";
import "../vendor/anatomy/iso.css";
import "../hardware.css";

const AZ = 62, EL = 32;
const U = 6.5;                      // map px per plan unit: wide enough that links show between chassis
const GW = 13, GD = 10, GH = 4.4;   // the twin's guest chassis
const PAD_H = 1.2, LIFT = 0.6;
const W = 1040;

export default function Topo3D({ data, selected, onSelect }: { data: Topo; selected?: string | null; onSelect?: (n: string) => void }) {
  const hw = usePoll<Hardware>("/api/hardware", 5000).data;
  const hd = new Map((hw?.devices ?? []).map((d) => [d.name, d]));
  const nodes = data.nodes.filter((n) => POS_WIDE[n.name]);
  const at = (n: string): [number, number] => [POS_WIDE[n][0] / U, POS_WIDE[n][1] / U];
  const ys = Object.values(POS_WIDE).map(([, y]) => y / U);
  const pads: Plan[] = SITES_WIDE.map((s) => ({ x: s.x / U, y: Math.min(...ys) - GD, w: s.w / U, d: Math.max(...ys) - Math.min(...ys) + 2 * GD + 3, r: 1.6 }));
  const { P, H } = useMemo(() => {
    const pts: Point3[] = pads.flatMap((p) => [[p.x, p.y, 0], [p.x + p.w, p.y, 0], [p.x, p.y + p.d, 0], [p.x + p.w, p.y + p.d, 0], [p.x, p.y, PAD_H + LIFT + GH + 2]] as Point3[]);
    // the frame takes the drawing's own aspect, so no empty band above or below it
    const fl = pts.map((q) => iso(q, fitProjection(pts, 1000, 1000, { pad: 0, azimuth: AZ, elevation: EL })));
    const aspect = (Math.max(...fl.map((f) => f[1])) - Math.min(...fl.map((f) => f[1]))) / (Math.max(...fl.map((f) => f[0])) - Math.min(...fl.map((f) => f[0])));
    const h = Math.round((W - 36) * aspect + 36);
    return { P: fitProjection(pts, W, h, { pad: 18, azimuth: AZ, elevation: EL }), H: h };
  }, []);
  const z0 = PAD_H + LIFT;
  const order = [...nodes].sort((a, b) => depthOf([...at(a.name), 0], P) - depthOf([...at(b.name), 0], P));
  const key = (n: string) => (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect?.(n); } };
  return (
    <svg className="iso iso-svg twin-iso topo3d" viewBox={`0 0 ${W} ${H}`} role="group" aria-label="Lab topology in 3D: campus and fabric">
      {SITES_WIDE.map((s, i) => (
        <g key={s.id}>
          <Solid paths={slabOf(pads[i], 0, PAD_H, P, 6, 0.3)} tone="mid" className="twin-host" />
          <g transform={topMatrix([pads[i].x + 1.4, pads[i].y + pads[i].d - 2.4], PAD_H, P, "x")} aria-hidden>
            <text x={0} y={1.6} className="twin-site">{s.label.toUpperCase()} · {s.sub}</text>
          </g>
        </g>
      ))}
      {data.links.filter((l) => POS_WIDE[l.a] && POS_WIDE[l.b]).map((l) => (
        <path key={l.id} d={onTop([at(l.a), at(l.b)], PAD_H, P)} fill="none" stroke={STATE_COLOR[l.state]} strokeWidth={3}
          strokeLinecap="round" strokeDasharray={l.state === "down" ? "6 5" : l.state === "unknown" ? "3 4" : undefined}>
          <title>{`${l.a} ${l.a_if ?? ""} — ${l.b} ${l.b_if ?? ""}: ${l.state}`}</title>
        </path>
      ))}
      {order.map((n) => {
        const [cx, cy] = at(n.name);
        const x = cx - GW / 2, y = cy - GD / 2;
        const d = hd.get(n.name);
        const sel = selected === n.name;
        const ports = d?.ports.slice(0, 7) ?? [];
        const rated = d?.ports.filter((p) => p.in_bps != null || p.out_bps != null) ?? [];
        const b = blink(rated.length ? { in_bps: rated.reduce((s, p) => s + (p.in_bps ?? 0), 0), out_bps: rated.reduce((s, p) => s + (p.out_bps ?? 0), 0) } : { in_bps: null, out_bps: null });
        const cpu = d?.cpu_pct ?? n.health?.cpu_pct ?? null;
        const label = `${n.name}, ${n.role} at ${n.site}: ${STATE_WORD[n.state] ?? n.state}${cpu != null ? `, CPU ${Math.round(cpu)}%` : ""}`;
        return (
          <g key={n.name} className={`twin-guest st-${n.state}${sel ? " sel" : ""}${onSelect ? " clickable" : ""}`}
            role={onSelect ? "button" : undefined} tabIndex={onSelect ? 0 : undefined} aria-label={label} aria-pressed={onSelect ? sel : undefined}
            onClick={onSelect ? () => onSelect(n.name) : undefined} onKeyDown={onSelect ? key(n.name) : undefined}>
            <title>{label}</title>
            <Solid paths={slabOf({ x, y, w: GW, d: GD, r: 1 }, z0, GH, P, 6, 0.4)} lit={sel} tone="hi" className="twin-guest-body" />
            <g transform={topMatrix([x + 1.3, y + 1.2], z0 + GH, P, "x")} aria-hidden>
              <text x={0} y={2.9} className="twin-name">{n.name}</text>
              <text x={0} y={5.0} className="twin-role">{d?.model === "vjunos-router" ? "vJunos-router" : d?.model === "vjunos-switch" ? "vJunos-switch" : n.kind ?? n.role}</text>
              <rect x={0} y={6.1} width={GW - 2.6} height={0.9} rx={0.45} className="twin-bar-bg" />
              {cpu != null && <rect x={0} y={6.1} width={Math.max(0.3, ((GW - 2.6) * Math.min(100, cpu)) / 100)} height={0.9} rx={0.45} className={`twin-bar${cpu >= 90 ? " hot" : ""}`} />}
              <text x={0} y={8.6} className="twin-cpu">{cpu == null ? "CPU —" : `CPU ${Math.round(cpu)}%`}</text>
            </g>
            <g transform={sideMatrix([x, y + GD], z0 + GH, P, "left")} aria-hidden>
              <circle cx={1.1} cy={1.3} r={0.5} className={`twin-led st-${n.state}`} />
              {ports.map((p, i) => <rect key={p.ifname} x={2.4 + i * 1.35} y={0.8} width={1.0} height={1.1} rx={0.15} className={`twin-port-led o-${operClass(p)}`} />)}
              <circle cx={GW - 1.1} cy={1.3} r={0.5} className={`twin-act${b.none ? " none" : b.idle ? " idle" : ""}`} style={b.dur ? { animationDuration: b.dur } : undefined} />
              <text x={1.1} y={GH - 0.9} className="twin-face-t">{d?.version ? `JUNOS ${d.version}` : "JUNOS —"}</text>
            </g>
          </g>
        );
      })}
    </svg>
  );
}
