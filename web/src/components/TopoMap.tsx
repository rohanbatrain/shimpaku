// Live topology map: deterministic, hand-placed positions per site (no physics), original SVG glyphs.
// Links are the lab TOML's intended links coloured by live evidence (LLDP, OSPF/BFD, stored port state).
import { useEffect, useState, type KeyboardEvent } from "react";

export type LinkState = "up" | "degraded" | "down" | "unknown";
export type TNode = {
  name: string; site: string; kind: string | null; role: "router" | "switch" | "spine" | "leaf"; version: string | null;
  state: LinkState; reachable: boolean | null;
  health: { cpu_pct: number | null; mem_pct: number | null; sampled_at: number | null; minutes: { n: number; good: number; bad: number }; last: { status: string; classifier: string | null; sub_classifier: string | null } | null; classifiers: { classifier: string; sub_classifier: string | null; minutes: number }[] } | null;
  routing: { ospf_full: number; ospf: number; bfd_up: number; bfd: number; bgp_established: number; bgp: number } | null;
  incidents: string[]; actions: { id: string; status: string; title: string; created_at: number }[]; links: string[];
};
export type TLink = { id: string; a: string; a_if: string | null; b: string; b_if: string | null; role: string | null; site: string; state: LinkState; good: string[]; bad: string[]; evidence: Record<string, string | null> };
export type Fixture = { device: string; ifname: string; vlan: number; keep_vlan: number; present: boolean | null; oper: string | null; admin: string | null; reason: string | null };
export type Topo = { generated_at: number; sites: string[]; nodes: TNode[]; links: TLink[]; fixture: Fixture; sources: Record<string, string>; demo?: boolean };

export const STATE_COLOR: Record<LinkState, string> = { up: "#01A982", degraded: "#D99A06", down: "#D93A30", unknown: "#8C9894" };
export const STATE_LABEL: Record<LinkState, string> = { up: "Up", degraded: "Degraded", down: "Down", unknown: "Unknown" };
export const STATE_TONE: Record<LinkState, "ok" | "warn" | "crit" | "neutral"> = { up: "ok", degraded: "warn", down: "crit", unknown: "neutral" };

// hand-tuned positions (viewBox 1040 × 400): campus left, fabric (2-spine / 2-leaf Clos) right
const POS_WIDE: Record<string, [number, number]> = {
  r1: [130, 120], r2: [340, 120], sw1: [130, 300],
  spine1: [640, 120], spine2: [900, 120], leaf1: [640, 300], leaf2: [900, 300],
};
const SITES_WIDE = [
  { id: "campus", label: "Campus", sub: "day-02 · r1 r2 sw1", x: 16, w: 470 },
  { id: "fabric", label: "Fabric", sub: "day-06 · 2-spine / 2-leaf Clos", x: 512, w: 512 },
];
const FIX: [number, number] = [340, 300];
const FABRIC = new Set(["spine1", "spine2", "leaf1", "leaf2"]);
// phones: the two sites stack (fabric under campus) so each stays legible
const POS_TALL = Object.fromEntries(Object.entries(POS_WIDE).map(([k, [x, y]]) => [k, FABRIC.has(k) ? [x - 496, y + 384] : [x, y]])) as Record<string, [number, number]>;
const SITES_TALL = SITES_WIDE.map((s) => (s.id === "fabric" ? { ...s, x: 16, y: 398 } : { ...s, w: 512 }));
function useTall() {
  const q = "(max-width: 600px)";
  const [t, setT] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => { const m = window.matchMedia(q); const h = () => setT(m.matches); m.addEventListener("change", h); return () => m.removeEventListener("change", h); }, []);
  return t;
}

function Glyph({ role }: { role: TNode["role"] }) {
  // original, minimal marks — ink on a white tile
  const ink = { fill: "none", stroke: "#16211C", strokeWidth: 2.2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (role === "router")
    return (
      <g {...ink}>
        <circle r="13" />
        <path d="M-7 -4h12m-3-3 3 3-3 3M7 4H-5m3 3-3-3 3-3" />
      </g>
    );
  if (role === "spine")
    return (
      <g {...ink}>
        <rect x="-15" y="-9" width="30" height="18" rx="3" />
        <path d="M-9 -9v-5M0 -9v-5M9 -9v-5M-9 9v5M0 9v5M9 9v5" />
      </g>
    );
  if (role === "leaf")
    return (
      <g {...ink}>
        <rect x="-15" y="-8" width="30" height="16" rx="3" />
        <path d="M-9 -8v-6M9 -8v-6" />
        <circle cx="-7" cy="0" r="1.6" fill="#16211C" /><circle cx="0" cy="0" r="1.6" fill="#16211C" /><circle cx="7" cy="0" r="1.6" fill="#16211C" />
      </g>
    );
  return (
    <g {...ink}>
      <rect x="-15" y="-9" width="30" height="18" rx="3" />
      <path d="M-8 -3h14m-3-2.5 3 2.5-3 2.5M8 3H-6m3 2.5-3-2.5 3-2.5" />
    </g>
  );
}

const mid = (a: [number, number], b: [number, number]) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const along = (a: [number, number], b: [number, number], d: number): [number, number] => {
  const L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  return [a[0] + ((b[0] - a[0]) * d) / L, a[1] + ((b[1] - a[1]) * d) / L];
};

export function linkText(l: TLink) {
  return `${l.a} ${l.a_if ?? ""} ↔ ${l.b} ${l.b_if ?? ""}: ${STATE_LABEL[l.state]}${l.bad.length ? ` — ${l.bad.join(", ")}` : ""}${l.good.length ? ` (${l.good.join(", ")})` : ""}`;
}

export default function TopoMap({ data, selected, onSelect, compact = false }: { data: Topo; selected?: string | null; onSelect?: (n: string) => void; compact?: boolean }) {
  const tall = useTall();
  const POS = tall ? POS_TALL : POS_WIDE;
  const SITES = (tall ? SITES_TALL : SITES_WIDE) as { id: string; label: string; sub: string; x: number; w: number; y?: number }[];
  const nodes = data.nodes.filter((n) => POS[n.name]);
  const fx = data.fixture;
  const fxColor = fx.present == null ? STATE_COLOR.unknown : fx.present ? STATE_COLOR.up : STATE_COLOR.down;
  const key = (n: string) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect?.(n); }
  };
  return (
    <svg className={`topo${compact ? " compact" : ""}`} viewBox={tall ? "0 0 544 784" : "0 0 1040 400"} role="group" aria-label="Lab topology: campus and fabric">
      <defs>
        <filter id="tshadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="1.5" stdDeviation="2" floodColor="#0E1A16" floodOpacity=".14" />
        </filter>
      </defs>
      {SITES.map((s) => (
        <g key={s.id} aria-hidden>
          <rect x={s.x} y={(s.y ?? 0) + 14} width={s.w} height="372" rx="12" className="topo-site" />
          <text x={s.x + 16} y={(s.y ?? 0) + 40} className="topo-site-t">{s.label}</text>
          {!compact && <text x={s.x + 16} y={(s.y ?? 0) + 58} className="topo-site-s">{s.sub}</text>}
        </g>
      ))}
      {/* links */}
      {data.links.map((l) => {
        const a = POS[l.a], b = POS[l.b];
        if (!a || !b) return null;
        const c = STATE_COLOR[l.state];
        const [mx, my] = mid(a, b);
        const vert = Math.abs(b[1] - a[1]) > Math.abs(b[0] - a[0]);
        const off: [number, number] = vert ? [14, 0] : [0, -9];
        const top = (p: [number, number], q: [number, number]) => (vert ? (p[1] < q[1] ? 86 : 50) : 62);
        const la = along(a, b, top(a, b)), lb = along(b, a, top(b, a));
        const hl = selected && (l.a === selected || l.b === selected);
        return (
          <g key={l.id} className={`topo-link s-${l.state}${hl ? " hl" : ""}`}>
            <title>{linkText(l)}</title>
            <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke="#fff" strokeWidth={hl ? 10 : 8} />
            <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={c} strokeWidth={hl ? 5 : 3.5} strokeDasharray={l.state === "unknown" ? "6 6" : l.state === "down" ? "2 7" : undefined} strokeLinecap="round" />
            {l.state !== "up" && (
              <g transform={`translate(${mx} ${my})`} aria-hidden>
                <circle r="9" fill={c} stroke="#fff" strokeWidth="2" />
                <text y="4" textAnchor="middle" className="topo-mark">{l.state === "unknown" ? "?" : "!"}</text>
              </g>
            )}
            {!compact && (
              <>
                <text x={la[0] + off[0]} y={la[1] + off[1]} className="topo-if" textAnchor={vert ? "start" : "middle"}>{l.a_if}</text>
                <text x={lb[0] + off[0]} y={lb[1] + off[1]} className="topo-if" textAnchor={vert ? "start" : "middle"}>{l.b_if}</text>
              </>
            )}
          </g>
        );
      })}
      {/* demo fixture trunk sw1 ge-0/0/5 */}
      {POS[fx.device] && (
        <g className={`topo-fix${fx.present === false ? " missing" : ""}`}>
          <title>{`${fx.device} ${fx.ifname} demo trunk: VLAN ${fx.vlan} ${fx.present == null ? "state unknown" : fx.present ? "present" : "missing"}`}</title>
          <line x1={POS[fx.device][0]} y1={POS[fx.device][1]} x2={FIX[0] - 70} y2={FIX[1]} stroke={fxColor} strokeWidth="3" strokeDasharray="1 6" strokeLinecap="round" />
          <rect x={FIX[0] - 72} y={FIX[1] - 24} width="160" height="48" rx="9" fill="#fff" stroke={fxColor} strokeWidth="2" filter="url(#tshadow)" className="topo-fix-box" />
          <text x={FIX[0] + 8} y={FIX[1] - 6} textAnchor="middle" className="topo-fix-t">{fx.ifname} · demo trunk</text>
          <text x={FIX[0] + 8} y={FIX[1] + 13} textAnchor="middle" className="topo-fix-v" fill={fx.present === false ? "#B3261E" : fx.present ? "#00785C" : "#56645E"}>
            VLAN {fx.vlan} {fx.present == null ? "unknown" : fx.present ? "present" : "missing"}
          </text>
        </g>
      )}
      {/* nodes */}
      {nodes.map((n) => {
        const [x, y] = POS[n.name];
        const c = STATE_COLOR[n.state];
        const sel = selected === n.name;
        const label = `${n.name}, ${n.role}, ${n.site}: ${STATE_LABEL[n.state]}${n.incidents.length ? `, ${n.incidents.length} open incident` : ""}`;
        return (
          <g key={n.name} className={`topo-node${sel ? " sel" : ""}`} transform={`translate(${x} ${y})`} role="button" tabIndex={onSelect ? 0 : -1}
            aria-label={label} aria-pressed={onSelect ? sel : undefined} onClick={() => onSelect?.(n.name)} onKeyDown={key(n.name)}>
            <rect className="topo-hit" x="-44" y="-44" width="88" height="104" rx="10" />
            {sel && <circle r="38" fill="none" stroke="#01A982" strokeWidth="3" opacity=".35" />}
            <circle r="30" fill="#fff" stroke={c} strokeWidth="4" filter="url(#tshadow)" />
            <Glyph role={n.role} />
            <g transform="translate(22 -22)" aria-hidden>
              <circle r="8" fill={c} stroke="#fff" strokeWidth="2" />
              {n.state !== "up" && <text y="3.5" textAnchor="middle" className="topo-mark">{n.state === "unknown" ? "?" : "!"}</text>}
            </g>
            <text y="50" textAnchor="middle" className="topo-name">{n.name}</text>
            {!compact && <text y="66" textAnchor="middle" className="topo-role">{n.role}</text>}
          </g>
        );
      })}
    </svg>
  );
}

export function Legend() {
  return (
    <div className="topo-legend" aria-label="Legend">
      {(["up", "degraded", "down", "unknown"] as LinkState[]).map((s) => (
        <span key={s}>
          <svg width="26" height="10" aria-hidden><line x1="2" y1="5" x2="24" y2="5" stroke={STATE_COLOR[s]} strokeWidth="3.5" strokeLinecap="round" strokeDasharray={s === "unknown" ? "6 6" : s === "down" ? "2 7" : undefined} /></svg>
          {STATE_LABEL[s]}
        </span>
      ))}
      <span className="weak">Links: lab TOML intent · state: LLDP, OSPF/BFD, port oper status</span>
    </div>
  );
}

/** Screen-reader (and narrow-screen) fallback: every link and its evidence as a table. */
export function LinkTable({ data, className }: { data: Topo; className?: string }) {
  return (
    <table className={`t ${className ?? ""}`}>
      <caption className="sr-only">Topology links and their live state</caption>
      <thead><tr><th>Link</th><th>Site</th><th>Role</th><th>State</th><th>Evidence</th></tr></thead>
      <tbody>
        {data.links.map((l) => (
          <tr key={l.id}>
            <td className="mono">{l.a} {l.a_if} ↔ {l.b} {l.b_if}</td>
            <td>{l.site}</td>
            <td>{l.role ?? "—"}</td>
            <td><span className={`pill ${STATE_TONE[l.state]}`}><span className="dot" aria-hidden />{STATE_LABEL[l.state]}</span></td>
            <td className="small" style={{ whiteSpace: "normal" }}>{[...l.bad, ...l.good].join(" · ") || "no evidence reported"}</td>
          </tr>
        ))}
        <tr>
          <td className="mono">{data.fixture.device} {data.fixture.ifname}</td>
          <td>campus</td><td>demo trunk</td>
          <td><span className={`pill ${data.fixture.present == null ? "neutral" : data.fixture.present ? "ok" : "crit"}`}><span className="dot" aria-hidden />VLAN {data.fixture.vlan} {data.fixture.present == null ? "unknown" : data.fixture.present ? "present" : "missing"}</span></td>
          <td className="small" style={{ whiteSpace: "normal" }}>{data.fixture.reason ?? `oper ${data.fixture.oper ?? "—"} (wired to nothing by design)`}</td>
        </tr>
      </tbody>
    </table>
  );
}
