import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../api";
import TopBar from "../components/TopBar";
import { Empty, Load } from "../components/ui";
import { ago, ratio } from "../format";

type Sys = { system: string; detect_recall: number | null; false_alarm: number | null; rca_top1: number; rca_top3: number; fix_rate: number; mttr_s: number | null; unsafe_actions: number; pass3: number };
type Bench = { published: boolean; run_id: string; frozen_tag: string; trials: number; null_trials: number; classes: number; generated_at: number; systems: Sys[]; per_class: { cls: string; trials: number; rca_top1: number; fix_rate: number }[]; notes?: string };

const S1 = "#01A982"; // ours
const S2 = "#5B6B8C"; // comparison

export default function Benchmark() {
  const q = useApi<Bench>("/api/bench/results");
  return (
    <>
      <TopBar title="Benchmark" crumbs="Blinded fault-injection runs, scored by a separate process" />
      <div className="content">
        <Load q={q} rows={6} empty={(b) => (b.published ? null : <Empty title="No published run yet">The scorer publishes here after a full run.</Empty>)}>
          {(b) => {
            const ours = b.systems[0];
            const chart = b.per_class.map((c) => ({ cls: c.cls, "Root cause top-1": Math.round(c.rca_top1 * 100), "Fixed and verified": Math.round(c.fix_rate * 100) }));
            return (
              <>
                <div className="grid4">
                  <div className="card"><div className="k">Root cause, top-1</div><div className="big">{ratio(ours.rca_top1)}</div><div className="small weak">top-3 {ratio(ours.rca_top3)}</div></div>
                  <div className="card"><div className="k">Fixed and verified</div><div className="big">{ratio(ours.fix_rate)}</div><div className="small weak">pass^3 {ratio(ours.pass3)}</div></div>
                  <div className="card"><div className="k">Unsafe actions</div><div className="big tone-ok">{ours.unsafe_actions}</div><div className="small weak">guardrail escapes</div></div>
                  <div className="card"><div className="k">Trials</div><div className="big">{b.trials}</div><div className="small weak">{b.null_trials} null · {b.classes} fault classes</div></div>
                </div>
                <div className="card flush">
                  <div className="card-h"><h2>Systems compared</h2><span className="small weak">run <span className="mono">{b.run_id}</span> · catalogue <span className="mono">{b.frozen_tag}</span> · {ago(b.generated_at)}</span></div>
                  <table className="t">
                    <thead><tr><th>System</th><th className="num">Detect recall</th><th className="num">False alarm</th><th className="num">RCA top-1</th><th className="num">RCA top-3</th><th className="num">Fixed</th><th className="num">pass^3</th><th className="num">MTTR</th><th className="num">Unsafe</th></tr></thead>
                    <tbody>
                      {b.systems.map((s, i) => (
                        <tr key={s.system} style={i === 0 ? { background: "#F2FBF8" } : undefined}>
                          <td><b>{s.system}</b></td>
                          <td className="num">{ratio(s.detect_recall)}</td>
                          <td className="num">{ratio(s.false_alarm)}</td>
                          <td className="num">{ratio(s.rca_top1)}</td>
                          <td className="num">{ratio(s.rca_top3)}</td>
                          <td className="num">{ratio(s.fix_rate)}</td>
                          <td className="num">{ratio(s.pass3)}</td>
                          <td className="num">{s.mttr_s == null ? "—" : `${Math.round(s.mttr_s / 60 * 10) / 10} min`}</td>
                          <td className={`num ${s.unsafe_actions ? "tone-crit" : ""}`}>{s.unsafe_actions}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="card">
                  <h2>By fault class · {ours.system}</h2>
                  <div role="img" aria-label="Root cause top-1 and fixed rate per fault class" style={{ height: 260 }}>
                    <ResponsiveContainer>
                      <BarChart data={chart} margin={{ top: 8, right: 8, bottom: 0, left: -14 }} barGap={2} barCategoryGap="22%">
                        <CartesianGrid vertical={false} stroke="#EEF1F0" />
                        <XAxis dataKey="cls" tick={{ fontSize: 11, fill: "#56645E" }} interval={0} tickLine={false} axisLine={{ stroke: "#DCE2DF" }} />
                        <YAxis domain={[0, 100]} unit="%" tick={{ fontSize: 11, fill: "#56645E" }} axisLine={false} tickLine={false} />
                        <Tooltip cursor={{ fill: "rgba(1,169,130,.06)" }} formatter={(v: any) => `${v}%`} contentStyle={{ fontSize: 12, borderRadius: 6 }} />
                        <Legend iconType="square" wrapperStyle={{ fontSize: 12 }} />
                        <Bar dataKey="Root cause top-1" fill={S1} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                        <Bar dataKey="Fixed and verified" fill={S2} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  {b.notes && <p className="small weak" style={{ marginBottom: 0 }}>{b.notes}</p>}
                </div>
              </>
            );
          }}
        </Load>
      </div>
    </>
  );
}
