// Guardrail funnel: a hand-built Sankey of every stored proposal through the guardrails and the
// write path. Node heights and ribbon widths are real counts; each band links to its actions.
import { Link, useNavigate } from "react-router";

export type FNode = { id: string; label: string; count: number; ids: string[]; stage: string; rule?: string; rule_name?: string | null; examples?: string[] };
export type FLink = { source: string; target: string; count: number; ids: string[] };
export type FunnelData = {
  generated_at: number; caller: string; window: string; window_s: number; total: number;
  by_caller: Record<string, number>; nodes: FNode[]; links: FLink[]; demo?: boolean;
};

const COL: Record<string, number> = {
  proposed: 0, passed: 1, awaiting: 2, automatic: 2, approved: 3, rejected: 3, cancelled: 3, waiting: 3,
  executed: 4, queued: 4, verified: 5, recurred: 5, failed: 5, rolled_back: 5, validating: 5, running: 5,
};
const colOf = (n: FNode) => (n.id.startsWith("blocked:") ? 1 : COL[n.id] ?? 5);
const ORDER = ["passed", "awaiting", "automatic", "approved", "waiting", "rejected", "cancelled", "executed", "queued", "verified", "validating", "running", "recurred", "rolled_back", "failed"];

export const TONE: Record<string, string> = {
  blocked: "#D93A30", verified: "#01A982", recurred: "#E0703A", failed: "#B3261E", rolled_back: "#C2452D",
  rejected: "#8C9894", cancelled: "#A9B4AF", waiting: "#D99A06", queued: "#D99A06",
};
export const toneOf = (stage: string) => TONE[stage] ?? "#2F8F74";

export function actionsHref(ids: string[], label: string) {
  return `/actions?ids=${encodeURIComponent(ids.join(","))}&band=${encodeURIComponent(label)}`;
}

type Placed = FNode & { x: number; y: number; h: number; outY: number; inY: number };

export default function Funnel({ data }: { data: FunnelData }) {
  const nav = useNavigate();
  const W = 1040, PAD = 24, NW = 14, GAP = 18;
  const cols: FNode[][] = [[], [], [], [], [], []];
  for (const n of data.nodes) cols[colOf(n)].push(n);
  const rank = (n: FNode) => (n.id.startsWith("blocked:") ? 100 + Number(n.rule?.replace(/\D/g, "") || 0) + (n.rule?.startsWith("T") ? -50 : 0) : ORDER.indexOf(n.id));
  cols.forEach((c) => c.sort((a, b) => rank(a) - rank(b)));
  const total = Math.max(1, data.total);
  const maxStack = Math.max(...cols.map((c) => c.length));
  const H = Math.max(280, 60 + maxStack * 46);
  const scale = (H - PAD * 2 - (maxStack - 1) * GAP) / total;
  const colX = (i: number) => PAD + 10 + i * ((W - PAD * 2 - 190) / 5);
  const placed: Record<string, Placed> = {};
  cols.forEach((c, i) => {
    let y = PAD;
    for (const n of c) {
      const h = Math.max(3, n.count * scale);
      placed[n.id] = { ...n, x: colX(i), y, h, outY: y, inY: y };
      y += h + GAP;
    }
  });
  const links = [...data.links].sort((a, b) => (placed[a.target]?.y ?? 0) - (placed[b.target]?.y ?? 0));
  const ribbons = links.map((l) => {
    const s = placed[l.source], t = placed[l.target];
    if (!s || !t) return null;
    const w = Math.max(1.5, l.count * scale);
    const y0 = s.outY + w / 2, y1 = t.inY + w / 2;
    s.outY += w; t.inY += w;
    const x0 = s.x + NW, x1 = t.x, mx = (x0 + x1) / 2;
    return { l, w, d: `M${x0},${y0} C${mx},${y0} ${mx},${y1} ${x1},${y1}`, color: toneOf(t.stage), label: `${s.label} → ${t.label}: ${l.count}` };
  });
  const go = (ids: string[], label: string) => nav(actionsHref(ids, label));
  return (
    <svg className="funnel" viewBox={`0 0 ${W} ${H}`} role="group" aria-label={`Guardrail funnel: ${data.total} proposals`}>
      {ribbons.map((r, i) => r && (
        <path key={i} d={r.d} className="funnel-band" stroke={r.color} strokeWidth={r.w} fill="none" tabIndex={0} role="link"
          aria-label={`${r.label}. Open these actions`} onClick={() => go(r.l.ids, r.label)}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), go(r.l.ids, r.label))}>
          <title>{r.label}</title>
        </path>
      ))}
      {Object.values(placed).map((n) => {
        const c = toneOf(n.stage);
        return (
          <g key={n.id} className="funnel-node" tabIndex={0} role="link" aria-label={`${n.label}: ${n.count}. Open these actions`}
            onClick={() => go(n.ids, n.label)} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), go(n.ids, n.label))}>
            <title>{n.rule_name ? `${n.label} — ${n.rule_name}` : n.label}</title>
            <rect x={n.x} y={n.y} width={NW} height={n.h} rx="3" fill={c} />
            <text x={n.x + NW + 6} y={n.y + Math.min(n.h / 2, 14) + 4} className="funnel-l">
              <tspan fontWeight="650">{n.count}</tspan> <tspan>{n.label}</tspan>
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Compact, mobile-safe summary for the Overview: the main path as steps, blocks by rule as chips. */
export function FunnelSummary({ data }: { data: FunnelData }) {
  const by = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
  const blocked = data.nodes.filter((n) => n.id.startsWith("blocked:"));
  const nb = blocked.reduce((a, n) => a + n.count, 0);
  const steps = ["proposed", "passed", "executed", "verified"].map((id) => by[id]).filter(Boolean) as FNode[];
  const bad = ["recurred", "failed", "rolled_back"].map((id) => by[id]).filter(Boolean) as FNode[];
  if (!data.total) return <span className="small weak">No proposals in this window.</span>;
  return (
    <div className="fsum">
      <div className="fsum-steps">
        {steps.map((n, i) => (
          <Link key={n.id} to={actionsHref(n.ids, n.label)} className="fsum-step" style={{ borderColor: toneOf(n.stage) }}>
            <b>{n.count}</b><span>{n.id === "passed" ? "passed" : n.label.toLowerCase()}</span>
            {i < steps.length - 1 && <i aria-hidden>→</i>}
          </Link>
        ))}
      </div>
      <div className="fsum-bar" aria-hidden>
        <i style={{ flex: data.total - nb, background: "#2F8F74" }} />
        {nb > 0 && <i style={{ flex: nb, background: TONE.blocked }} />}
      </div>
      <div className="fsum-chips">
        {nb > 0 ? <span className="small weak">{nb} blocked:</span> : <span className="small weak">nothing blocked</span>}
        {blocked.map((n) => (
          <Link key={n.id} className="chip fsum-chip" to={actionsHref(n.ids, n.label)} title={n.rule_name ?? undefined}>{n.rule} · {n.count}</Link>
        ))}
        {bad.map((n) => (
          <Link key={n.id} className="chip fsum-chip bad" to={actionsHref(n.ids, n.label)}>{n.label.toLowerCase()} · {n.count}</Link>
        ))}
      </div>
    </div>
  );
}
