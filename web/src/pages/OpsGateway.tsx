import { useEffect } from "react";
import { useApi } from "../api";
import TopBar from "../components/TopBar";
import { Load, Pill } from "../components/ui";
import { time } from "../format";
import "../ops.css";

type Row = { name: string; requests: number; errors: number; error_pct: number | null; spend_usd: number; tokens: number; p50_ms: number | null; p95_ms: number | null };
type Gw = {
  demo: boolean; connected: boolean; cached: boolean; fetched_at: number; window_h: number; errors: string[]; base?: string;
  health: { alive: boolean; ready: boolean; db: string | null; version: string | null };
  totals?: Omit<Row, "name">; models?: Row[]; keys?: Row[]; sampled?: { rows: number; total: number; capped: boolean };
  models_health?: { model: string; healthy: boolean }[] | null;
};

const usd = (v: number) => (v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`);
const ms = (v: number | null) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v} ms`);

function Table({ title, rows, total }: { title: string; rows: Row[]; total: number }) {
  return (
    <div className="card flush">
      <div className="card-h"><h2>{title}</h2></div>
      <div className="ops-scroll" tabIndex={0} role="region" aria-label={`${title} table, scrolls sideways`}>
        <table className="t">
          <thead><tr><th>{title === "Per model" ? "Model" : "Key"}</th><th className="num">Requests</th><th className="num">Errors</th><th className="num">p50</th><th className="num">p95</th><th className="num">Spend</th><th className="ops-barcol">Share</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td className="mono">{r.name}</td>
                <td className="num">{r.requests.toLocaleString()}</td>
                <td className={`num${(r.error_pct ?? 0) >= 10 ? " tone-crit" : ""}`}>{r.errors} <span className="weak small">{r.error_pct == null ? "" : `${r.error_pct}%`}</span></td>
                <td className="num">{ms(r.p50_ms)}</td>
                <td className="num">{ms(r.p95_ms)}</td>
                <td className="num">{usd(r.spend_usd)}</td>
                <td className="ops-barcol"><span className="ops-bar" style={{ width: `${total ? (100 * r.spend_usd) / total : 0}%` }} aria-hidden /></td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={7} className="weak">No requests in the window.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function OpsGateway() {
  const q = useApi<Gw>("/api/ops/gateway");
  useEffect(() => { const t = setInterval(q.reload, 30000); return () => clearInterval(t); }, [q.reload]);
  return (
    <>
      <TopBar title="LLM gateway" crumbs="Admin · LiteLLM's own APIs · refreshed every 30 s" />
      <div className="content">
        <Load q={q} rows={6}>
          {(g) => (
            <>
              <div className="card ops-health">
                <div><span className="g-label">Gateway</span><div className="mono">{g.base ?? "—"}</div></div>
                <div><span className="g-label">Process</span><div><Pill tone={g.health.alive ? "ok" : "crit"}>{g.health.alive ? "alive" : "unreachable"}</Pill></div></div>
                <div><span className="g-label">Ready</span><div><Pill tone={g.health.ready ? "ok" : "warn"}>{g.health.ready ? "ready" : "not ready"}</Pill></div></div>
                <div><span className="g-label">Database</span><div>{g.health.db ?? "—"}</div></div>
                <div><span className="g-label">Version</span><div className="mono">{g.health.version ?? "—"}</div></div>
                <div><span className="g-label">Fetched</span><div className="mono">{time(g.fetched_at)}{g.cached ? " (cached)" : ""}</div></div>
              </div>
              {g.errors.length > 0 && <div className="notice warn">Some gateway calls failed: {g.errors.join("; ")}</div>}
              {!g.connected ? (
                <div className="notice info"><b>Connect an admin key</b> to see requests, latency, errors and spend. Set <span className="mono">SHIMPAKU_LLM_ADMIN_KEY</span> in <span className="mono">~/.config/shimpaku/env</span> on the host and restart shimpakud. Only health is shown without it; the key is never sent to the browser.</div>
              ) : g.totals && (
                <>
                  <div className="grid4">
                    <div className="card ops-kpi"><div className="g-label">Requests, {g.window_h} h</div><div className="ops-kpi-n">{g.totals.requests.toLocaleString()}</div></div>
                    <div className="card ops-kpi"><div className="g-label">Errors</div><div className={`ops-kpi-n${(g.totals.error_pct ?? 0) >= 5 ? " tone-crit" : ""}`}>{g.totals.error_pct ?? 0}%</div><div className="g-sub">{g.totals.errors} failed</div></div>
                    <div className="card ops-kpi"><div className="g-label">Latency p50 / p95</div><div className="ops-kpi-n">{ms(g.totals.p50_ms)}</div><div className="g-sub">p95 {ms(g.totals.p95_ms)}</div></div>
                    <div className="card ops-kpi"><div className="g-label">Spend, {g.window_h} h</div><div className="ops-kpi-n">{usd(g.totals.spend_usd)}</div><div className="g-sub">{g.totals.tokens.toLocaleString()} tokens</div></div>
                  </div>
                  {g.sampled?.capped && <div className="notice info small">Based on the newest {g.sampled.rows.toLocaleString()} of {g.sampled.total.toLocaleString()} requests in the window.</div>}
                  <Table title="Per model" rows={g.models ?? []} total={g.totals.spend_usd} />
                  <Table title="Per key alias" rows={g.keys ?? []} total={g.totals.spend_usd} />
                  {g.models_health && g.models_health.length > 0 && (
                    <div className="card"><h2>Model health (last stored check)</h2>
                      <div className="ops-chips">{g.models_health.map((m) => <Pill key={m.model} tone={m.healthy ? "ok" : "crit"}>{m.model}</Pill>)}</div>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </Load>
      </div>
    </>
  );
}
