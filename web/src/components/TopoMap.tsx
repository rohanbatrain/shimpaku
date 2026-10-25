// Live topology map: deterministic, hand-placed positions per site (no physics), original SVG glyphs.
// Links are the lab TOML's intended links coloured by live evidence (LLDP, OSPF/BFD, stored port state).
import { useEffect, useState, type KeyboardEvent } from "react";
import { fmtBps, type TrafficLink } from "../live";
import "../live.css";
import "../vizfix.css";

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
export const POS_WIDE: Record<string, [number, number]> = {
  r1: [130, 120], r2: [340, 120], sw1: [130, 300],
  spine1: [640, 120], spine2: [900, 120], leaf1: [640, 300], leaf2: [900, 300],
};
export const SITES_WIDE = [
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

// live traffic overlay: width grows with log(rate); dashes move a→b / b→a (CSS, off under reduced motion)
const flowW = (v: number) => Math.max(1.4, Math.min(5, 1.4 + 0.95 * Math.log10(1 + v / 1e4)));
const flowDur = (v: number) => `${Math.max(0.5, Math.min(4, 4.6 - 0.55 * Math.log10(1 + v))).toFixed(2)}s`;
const UTIL_COLOR = (u: number | null) => (u == null ? "#2F6690" : u < 0.5 ? "#01A982" : u < 0.8 ? "#D99A06" : "#D93A30");
export const trafficText = (t: TrafficLink) =>
  `${t.a} → ${t.b}: ${fmtBps(t.ab_bps)} · ${t.b} → ${t.a}: ${fmtBps(t.ba_bps)}${t.speed_bps ? "" : " · link speed unknown"}`;

function Flow({ from, to, bps, util, side }: { from: [number, number]; to: [number, number]; bps: number | null; util: number | null; side: 1 | -1 }) {
  if (bps == null) return null;
  const L = Math.hypot(to[0] - from[0], to[1] - from[1]) || 1;
  const off = 6.5 * side;
  const nx = (-(to[1] - from[1]) / L) * off, ny = ((to[0] - from[0]) / L) * off;
  // stop short of the node circles (r = 30) so the dashes read as travelling between devices
  const s = along(from, to, 34), e = along(to, from, 34);
  const idle = bps < 1;
  return (
    <line className={`topo-flow${idle ? " idle" : ""}`} x1={s[0] + nx} y1={s[1] + ny} x2={e[0] + nx} y2={e[1] + ny}
      stroke={UTIL_COLOR(util)} strokeWidth={idle ? 1.2 : flowW(bps)} style={{ ["--dur" as string]: flowDur(bps) }} />
  );
}

/** `focus`: highlight these nodes / link ids and dim the rest (blast radius on the root-cause page). */
export type TopoFocus = { nodes: string[]; links: string[] } | null;

export default function TopoMap({ data, selected, onSelect, compact = false, traffic, onLink, selectedLink, focus = null, layout, maxHeight }: { data: Topo; layout?: "wide" | "tall"; maxHeight?: number; focus?: TopoFocus; selected?: string | null; onSelect?: (n: string) => void; compact?: boolean; traffic?: TrafficLink[] | null; onLink?: (id: string) => void; selectedLink?: string | null }) {
  const fcls = (on: boolean) => (focus ? (on ? " focus" : " dim") : "");
  const tmap = new Map((traffic ?? []).map((t) => [t.id, t]));
  const phone = useTall();
  const tall = layout ? layout === "tall" : phone;
  const POS = tall ? POS_TALL : POS_WIDE;
  const SITES = (tall ? SITES_TALL : SITES_WIDE) as { id: string; label: string; sub: string; x: number; w: number; y?: number }[];
  const nodes = data.nodes.filter((n) => POS[n.name]);
  // the top row of each site names itself above the glyph: its links run downward through the usual label spot
  const topY = (n: TNode) => Math.min(...nodes.filter((m) => FABRIC.has(m.name) === FABRIC.has(n.name)).map((m) => POS[m.name][1]));
  const fx = data.fixture;
  const fxColor = fx.present == null ? STATE_COLOR.unknown : fx.present ? STATE_COLOR.up : STATE_COLOR.down;
  const key = (n: string) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect?.(n); }
  };
  return (
    <svg className={`topo${compact ? " compact" : ""}${tall ? " tall" : ""}`} viewBox={tall ? "0 0 544 784" : "0 0 1040 400"} style={maxHeight ? { maxHeight } : undefined} role="group" aria-label="Lab topology: campus and fabric">
      <defs>
        <filter id="tshadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="1.5" stdDeviation="2" floodColor="#0E1A16" floodOpacity=".14" />
        </filter>
      </defs>
      {SITES.map((s) => (
        <g key={s.id} aria-hidden>
          <rect x={s.x} y={(s.y ?? 0) + 14} width={s.w} height="372" rx="12" className="topo-site" />
          <text x={s.x + 16} y={(s.y ?? 0) + 40} className="topo-site-t">{s.label}{!compact && <tspan className="topo-site-s" dx="12">{s.sub}</tspan>}</text>
        </g>
      ))}
      {/* links */}
      {data.links.map((l) => {
        const a = POS[l.a], b = POS[l.b];
        if (!a || !b) return null;
        const c = STATE_COLOR[l.state];
        const [mx, my] = mid(a, b);
        const vert = Math.abs(b[1] - a[1]) > Math.abs(b[0] - a[0]);
        // vertical links: port labels on the outer side of their column, so they never meet the
        // labels of the crossing diagonals; diagonals: labels further out, clear of the crossing
        const st = SITES.find((x) => x.id === l.site);
        const side = st && a[0] < st.x + st.w / 2 ? -1 : 1;
        const diag = !vert && Math.abs(b[1] - a[1]) > 20;
        const off: [number, number] = vert ? [side * 14, 0] : [0, -16];
        const anchor = vert ? (side < 0 ? "end" : "start") : "middle";
        const top = (p: [number, number], q: [number, number]) => (vert ? (p[1] < q[1] ? 92 : 56) : 62);
        // a diagonal's port labels sit beside the line, on the side it leaves: above a line that runs
        // down from its node, below one that runs up; text extends the way the line goes, so the gap widens
        const lab = (p: [number, number], q: [number, number]) => {
          if (!diag) { const [x, y] = along(p, q, top(p, q)); return { x: x + off[0], y: y + off[1], anchor }; }
          const [x, y] = along(p, q, tall ? 46 : 70);
          const down = q[1] > p[1], right = q[0] > p[0];
          return { x: x + (right ? 4 : -4), y: y + (down ? -9 : 17), anchor: right ? "start" : "end" };
        };
        const la = lab(a, b), lb = lab(b, a);
        const hl = (selected && (l.a === selected || l.b === selected)) || selectedLink === l.id;
        const tr = tmap.get(l.id);
        return (
          <g key={l.id} className={`topo-link s-${l.state}${hl ? " hl" : ""}${onLink ? " clickable" : ""}${fcls(!!focus?.links.includes(l.id))}`}
            onClick={onLink ? () => onLink(l.id) : undefined} role={onLink ? "button" : undefined} tabIndex={onLink ? 0 : undefined}
            aria-label={onLink ? `${linkText(l)}${tr ? `. ${trafficText(tr)}` : ""}` : undefined}
            onKeyDown={onLink ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onLink(l.id); } } : undefined}>
            <title>{`${linkText(l)}${tr ? `\n${trafficText(tr)}` : ""}`}</title>
            <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke="#fff" strokeWidth={hl ? 10 : 8} />
            <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={c} strokeWidth={hl ? 5 : 3.5} strokeDasharray={l.state === "unknown" ? "6 6" : l.state === "down" ? "2 7" : undefined} strokeLinecap="round" />
            {tr && (
              <g aria-hidden>
                <Flow from={a} to={b} bps={tr.ab_bps} util={tr.util_ab} side={1} />
                <Flow from={b} to={a} bps={tr.ba_bps} util={tr.util_ba} side={1} />
              </g>
            )}
            {onLink && <line className="topo-link-hit" x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} />}
            {l.state !== "up" && (
              <g transform={`translate(${mx} ${my})`} aria-hidden>
                <circle r="9" fill={c} stroke="#fff" strokeWidth="2" />
                <text y="4" textAnchor="middle" className="topo-mark">{l.state === "unknown" ? "?" : "!"}</text>
              </g>
            )}
            {!compact && (
              <>
                <text x={la.x} y={la.y} className="topo-if" textAnchor={la.anchor as "start" | "middle" | "end"}>{l.a_if}</text>
                <text x={lb.x} y={lb.y} className="topo-if" textAnchor={lb.anchor as "start" | "middle" | "end"}>{l.b_if}</text>
              </>
            )}
          </g>
        );
      })}
      {/* demo fixture trunk sw1 ge-0/0/5 */}
      {POS[fx.device] && (
        <g className={`topo-fix${fx.present === false ? " missing" : ""}${fcls(false)}`}>
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
        const above = y === topY(n);
        const label = `${n.name}, ${n.role}, ${n.site}: ${STATE_LABEL[n.state]}${n.incidents.length ? `, ${n.incidents.length} open incident` : ""}`;
        return (
          <g key={n.name} className={`topo-node${sel ? " sel" : ""}${fcls(!!focus?.nodes.includes(n.name))}`} transform={`translate(${x} ${y})`} role="button" tabIndex={onSelect ? 0 : -1}
            aria-label={label} aria-pressed={onSelect ? sel : undefined} onClick={() => onSelect?.(n.name)} onKeyDown={key(n.name)}>
            <rect className="topo-hit" x="-50" y={above ? -66 : -44} width="100" height="120" rx="10" />
            {sel && <circle r="38" fill="none" stroke="#01A982" strokeWidth="3" opacity=".35" />}
            {focus?.nodes.includes(n.name) && <circle r="40" className="topo-focus-ring" />}
            <circle r="30" fill="#fff" stroke={c} strokeWidth="4" filter="url(#tshadow)" />
            <Glyph role={n.role} />
            <g transform="translate(22 -22)" aria-hidden>
              <circle r="8" fill={c} stroke="#fff" strokeWidth="2" />
              {n.state !== "up" && <text y="3.5" textAnchor="middle" className="topo-mark">{n.state === "unknown" ? "?" : "!"}</text>}
            </g>
            {above ? (
              <text y="-46" textAnchor="middle" className="topo-name">{n.name}{!compact && <tspan className="topo-role" dx="7">{n.role}</tspan>}</text>
            ) : (
              <>
                <text y="54" textAnchor="middle" className="topo-name">{n.name}</text>
                {!compact && <text y="70" textAnchor="middle" className="topo-role">{n.role}</text>}
              </>
            )}
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

/** Screen-reader fallback and the Links list: a table on wide screens, one card per link on phones. */
export function LinkTable({ data, className }: { data: Topo; className?: string }) {
  const fx = data.fixture;
  return (
    <table className={`t linkcards ${className ?? ""}`}>
      <caption className="sr-only">Topology links and their live state</caption>
      <thead><tr><th>Link</th><th>Site</th><th>Role</th><th>State</th><th>Evidence</th></tr></thead>
      <tbody>
        {data.links.map((l) => (
          <tr key={l.id}>
            <td className="mono lc-link" data-label="Link">{l.a} {l.a_if} ↔ {l.b} {l.b_if}</td>
            <td data-label="Site">{l.site}</td>
            <td data-label="Role">{l.role ?? "—"}</td>
            <td className="lc-state" data-label="State"><span className={`pill ${STATE_TONE[l.state]}`}><span className="dot" aria-hidden />{STATE_LABEL[l.state]}</span></td>
            <td className="small lc-ev" data-label="Evidence">{[...l.bad, ...l.good].join(" · ") || "no evidence reported"}</td>
          </tr>
        ))}
        <tr>
          <td className="mono lc-link" data-label="Link">{fx.device} {fx.ifname}</td>
          <td data-label="Site">campus</td><td data-label="Role">demo trunk</td>
          <td className="lc-state" data-label="State"><span className={`pill ${fx.present == null ? "neutral" : fx.present ? "ok" : "crit"}`}><span className="dot" aria-hidden />VLAN {fx.vlan} {fx.present == null ? "unknown" : fx.present ? "present" : "missing"}</span></td>
          <td className="small lc-ev" data-label="Evidence">{fx.reason ?? `oper ${fx.oper ?? "—"} (wired to nothing by design)`}</td>
        </tr>
      </tbody>
    </table>
  );
}
