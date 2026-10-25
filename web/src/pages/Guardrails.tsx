import Info from "../components/Info";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import TopBar from "../components/TopBar";
import Funnel, { actionsHref, toneOf, type FunnelData } from "../components/Funnel";
import { usePoll } from "../components/poll";
import { Empty, Load, Seg } from "../components/ui";
import { time } from "../format";
import "../explain.css";

const CALLERS = [
  { value: "all", label: "All" }, { value: "agent", label: "Agents" }, { value: "assistant", label: "Shim" },
  { value: "tester", label: "Testers" }, { value: "operator", label: "Operators" },
] as const;
const WINDOWS = [{ value: "1h", label: "1 h" }, { value: "24h", label: "24 h" }, { value: "7d", label: "7 d" }, { value: "all", label: "All" }] as const;
type C = (typeof CALLERS)[number]["value"];
type Wn = (typeof WINDOWS)[number]["value"];

export default function Guardrails() {
  const [sp, setSp] = useSearchParams();
  const caller = (sp.get("caller") as C) || "all";
  const win = (sp.get("window") as Wn) || "7d";
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); n.set(k, v); setSp(n, { replace: true }); };
  const q = usePoll<FunnelData>(`/api/explain/funnel?caller=${caller}&window=${win}`, 15000);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <TopBar title="Guardrails" crumbs="Every action proposal: where the guardrails stopped it, or how far it got" />
      <div className="content">
        <div className="card ex-filters">
          <div>
            <div className="k">Proposed by</div>
            <Seg label="Caller kind" value={caller} onChange={(v) => set("caller", v)}
              options={CALLERS.map((c) => ({ value: c.value, label: q.data && c.value !== "all" ? `${c.label} ${q.data.by_caller[c.value] ?? 0}` : c.label }))} />
          </div>
          <div>
            <div className="k">Window</div>
            <Seg label="Time window" value={win} onChange={(v) => set("window", v)} options={WINDOWS.map((w) => ({ ...w }))} />
          </div>
          <span style={{ flex: 1 }} />
          {q.data && <span className="small weak">computed {time(q.data.generated_at)} from {q.data.total} stored proposals</span>}
        </div>
        <Load q={q} rows={6} empty={(d) => (d.total ? null : <div className="card"><Empty title="No proposals">Nothing was proposed by this caller kind in this window.</Empty></div>)}>
          {(d) => {
            const blocked = d.nodes.filter((n) => n.id.startsWith("blocked:"));
            const nb = blocked.reduce((a, n) => a + n.count, 0);
            const rest = d.nodes.filter((n) => !n.id.startsWith("blocked:") && n.id !== "proposed");
            return (
              <>
                <div className="card">
                  <div className="ex-head">
                    <h2 style={{ margin: 0 }}>Funnel<Info k="guardrails" /></h2>
                    <span className="small weak">height = number of proposals · click a band or a stage to open those actions</span>
                  </div>
                  <div className="funnel-scroll"><Funnel data={d} /></div>
                </div>
                <div className="grid2">
                  <div className="card">
                    <h2>Blocked by guardrail · {nb} of {d.total}</h2>
                    {blocked.length === 0 && <span className="small weak">No proposal was blocked in this window.</span>}
                    <div className="ex-rules">
                      {blocked.map((n) => (
                        <div key={n.id} className="ex-rule">
                          <button className="ex-rule-h" aria-expanded={open === n.id} onClick={() => setOpen(open === n.id ? null : n.id)}>
                            <span className="ex-rule-id" style={{ background: toneOf("blocked") }}>{n.rule}</span>
                            <span className="ex-rule-n">{n.rule_name ?? "guardrail"}</span>
                            <b>{n.count}</b>
                          </button>
                          {(open === n.id || blocked.length <= 3) && (
                            <div className="ex-rule-b">
                              {(n.examples ?? []).map((e, i) => <div key={i} className="mono small ex-reason">{e}</div>)}
                              <Link className="small" to={actionsHref(n.ids, n.label)}>Open {n.count} action{n.count === 1 ? "" : "s"} →</Link>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="card">
                    <h2>After the guardrails<Info k="approval" /></h2>
                    <table className="t">
                      <thead><tr><th>Stage</th><th className="num">Actions</th><th className="num">Share</th></tr></thead>
                      <tbody>
                        {rest.map((n) => (
                          <tr key={n.id}>
                            <td><span className="ex-dot" style={{ background: toneOf(n.stage) }} /><Link to={actionsHref(n.ids, n.label)}>{n.label}</Link></td>
                            <td className="num">{n.count}</td>
                            <td className="num">{Math.round((n.count / d.total) * 100)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
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
