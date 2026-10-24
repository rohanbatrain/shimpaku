import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../api";
import TopBar from "../components/TopBar";
import { Empty, Load } from "../components/ui";
import { ago, ratio } from "../format";

type Sys = { system: string; detect_recall: number | null; false_alarm: number | null; rca_top1: number; rca_top3: number; fix_rate: number; mttr_s: number | null; unsafe_actions: number; pass3: number };
type Scen = { id: string; title: string; status: string; trials: number; passes: number; fails: number; blocked: number; time_to_effect_s_median: number | null; time_to_restore_s_median: number | null; time_to_restore_s_max: number | null; config_restored_all: boolean | null; last: number | null };
type SelfTest = { generated_at: number | null; files: string[]; scenarios: Scen[]; totals: { scenarios: number; trials: number; passes: number; fails: number; blocked: number; config_restored_all: boolean | null } };
type Bench = { selftest?: SelfTest | null; runs?: unknown[]; published: boolean; run_id: string; frozen_tag: string; trials: number; null_trials: number; classes: number; generated_at: number; systems: Sys[]; per_class: { cls: string; trials: number; rca_top1: number; fix_rate: number }[]; notes?: string };

const S1 = "#01A982"; // ours
const S2 = "#5B6B8C"; // comparison

const sec = (v: number | null) => (v == null ? "—" : `${v} s`);

function SelfTestView({ t }: { t: SelfTest }) {
  const x = t.totals;
  return (
    <>
      <div className="grid4">
        <div className="card"><div className="k">Harness self-test</div><div className={`big ${x.fails ? "tone-crit" : "tone-ok"}`}>{x.passes}/{x.trials}</div><div className="small weak">trials passed</div></div>
        <div className="card"><div className="k">Scenarios</div><div className="big">{x.scenarios}</div><div className="small weak">fault-injection scenarios</div></div>
        <div className="card"><div className="k">Failed · blocked</div><div className={`big ${x.fails || x.blocked ? "tone-crit" : ""}`}>{x.fails} · {x.blocked}</div><div className="small weak">trials</div></div>
        <div className="card"><div className="k">Config restored</div><div className="big">{x.config_restored_all == null ? "—" : x.config_restored_all ? "all" : "not all"}</div><div className="small weak">after every trial</div></div>
      </div>
      <div className="card flush">
        <div className="card-h"><h2>Self-test by scenario</h2><span className="small weak">checks that each injected fault takes effect and is restored; this does not score detection or repair · {ago(t.generated_at)}</span></div>
        <table className="t">
          <thead><tr><th>Scenario</th><th>Title</th><th className="num">Passes</th><th className="num">Fails</th><th className="num">Blocked</th><th className="num">Effect (median)</th><th className="num">Restore (median / max)</th><th>Config restored</th></tr></thead>
          <tbody>
            {t.scenarios.map((s) => (
              <tr key={s.id}>
                <td className="mono">{s.id}</td>
                <td>{s.title}</td>
                <td className="num">{s.passes}/{s.trials}</td>
                <td className={`num ${s.fails ? "tone-crit" : ""}`}>{s.fails}</td>
                <td className="num">{s.blocked}</td>
                <td className="num">{sec(s.time_to_effect_s_median)}</td>
                <td className="num">{sec(s.time_to_restore_s_median)} / {sec(s.time_to_restore_s_max)}</td>
                <td>{s.config_restored_all == null ? "—" : s.config_restored_all ? "yes" : <b className="tone-crit">no</b>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function Benchmark() {
  const q = useApi<Bench>("/api/bench/results");
  return (
    <>
      <TopBar title="Benchmark" crumbs="Blinded fault-injection runs, scored by a separate process" />
      <div className="content">
        <Load q={q} rows={6} empty={(b) => (b.published || b.selftest ? null : <Empty title="No published run yet">The scorer publishes here after a full run.</Empty>)}>
          {(b) => {
            if (!b.published)
              return (
                <>
                  <div className="notice info"><b>No scored run yet.</b> No detection, root-cause or repair scores have been published{b.runs?.length ? ` (${b.runs.length} run file(s) present, none scored)` : ""}. Below is the fault-injection harness self-test only.</div>
                  {b.selftest && <SelfTestView t={b.selftest} />}
                </>
              );
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
                {b.selftest && <SelfTestView t={b.selftest} />}
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
