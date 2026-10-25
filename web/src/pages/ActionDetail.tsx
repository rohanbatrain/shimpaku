import { useState } from "react";
import { Link, useParams } from "react-router";
import { Layer } from "grommet";
import { api, useApi, useSse } from "../api";
import TopBar from "../components/TopBar";
import { SleChart } from "../components/LazyCharts";
import { Busy, Diff, Load, Pill, StatusPill, ToneIcon } from "../components/ui";
import { time } from "../format";
import { useSession } from "../session";

type Act = {
  id: string; kind: string; tier: string; title: string; device: string; site: string | null; incident_id?: string | null;
  args: Record<string, unknown>; reason: string; status: string; mode: string | null; proposed_by: string; approved_by?: string | null;
  created_at: number; confirm_minutes?: number; validation_minutes?: number; committed_at?: number | null;
  steps: { state: string; t: number | null }[]; guardrails: { id: string; name: string; passed: boolean; detail?: string }[];
  diff: string | null; validation: { t: number; level: string; text: string }[]; verifier?: { status: string; detail: string } | null;
  series?: { t: number; value: number }[] | null; series_kind?: string | null; demo?: boolean;
  commits?: { comment?: string; leg?: string; confirm_min?: number | null; junos_index0_time?: string; seconds?: number; ts?: number }[];
};

const DOT: Record<string, string> = { bad: "#D93A30", info: "#5B6B8C", good: "#1B7F3A", pending: "#3A9CC0" };
const STEP_LABEL: Record<string, string> = { proposed: "Proposed", approved: "Approved", committed: "Committed", validating: "Validating", verified: "Verified", recurred: "Recurred", blocked: "Blocked", failed: "Failed", rejected: "Rejected", cancelled: "Cancelled", rolled_back: "Rolled back" };

export default function ActionDetail() {
  const { id = "" } = useParams();
  const q = useApi<Act>(`/api/actions/${id}`);
  const { who } = useSession();
  const [confirm, setConfirm] = useState<null | "approve" | "reject">(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useSse("/api/stream/actions", "actions", () => q.reload());

  const run = async (what: "approve" | "reject") => {
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/actions/${id}/${what}`, { method: "POST" });
      q.reload();
      setConfirm(null);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const a = q.data;
  const cur = a ? a.steps.findIndex((s) => !s.t) : -1;
  const validatingFor = a?.committed_at ? Math.round((Date.now() / 1000 - a.committed_at) / 60) : null;

  return (
    <>
      <TopBar title={a ? <>Action {a.id} · {a.title}</> : `Action ${id}`} crumbs={<><Link to="/actions">Actions</Link>{a?.incident_id && <> / <Link to={`/root-cause/${a.incident_id}`}>{a.incident_id}</Link></>}</>}>
        {a && <StatusPill status={a.status} />}
        {a && a.kind === "add_vlan_to_trunk" && a.args?.ifname === "ge-0/0/5" && a.args?.vlan === 999 && <Link className="btn" to={`/theatre?action=${a.id}`}>Watch in theatre</Link>}
        {a && <Pill tone={a.tier === "T2" ? "warn" : "neutral"}>{a.mode ? `${a.tier} · ${a.mode}` : a.tier}</Pill>}
        {a && ["proposed", "awaiting_approval"].includes(a.status) && (
          <>
            <button className="btn primary" disabled={!who.can_approve} title={who.can_approve ? "" : who.replay ? "Read-only replay: approvals run only in the private lab" : "Approval needs a signed-in dashboard user"} onClick={() => setConfirm("approve")}>
              Approve and commit
            </button>
            <button className="btn danger" onClick={() => setConfirm("reject")}>Reject</button>
          </>
        )}
      </TopBar>
      <div className="content">
        <Load q={q} rows={8}>
          {(a) => (
            <>
              <div className="card">
                <div className="steps" aria-label="Lifecycle">
                  {a.steps.map((s, i) => (
                    <div key={s.state} className={s.t ? "done" : i === cur ? "cur" : ""} aria-current={i === cur ? "step" : undefined}>
                      <b>{STEP_LABEL[s.state] ?? s.state}</b>
                      {s.t ? time(s.t) : i === cur ? (s.state === "verified" && validatingFor != null ? `in validation · ${validatingFor} of ${a.validation_minutes} min` : "next") : "—"}
                    </div>
                  ))}
                </div>
              </div>
              {err && <div className="notice crit" role="alert">{err}</div>}
              <div className="grid2">
                <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                  <div className="card">
                    <h2>Why</h2>
                    <p style={{ margin: "0 0 10px" }}>{a.reason}</p>
                    <dl className="kv kv-narrow">
                      <dt>Device</dt><dd className="mono">{a.site ? `${a.device} · ${a.site}` : a.device}</dd>
                      <dt>Tool</dt><dd className="mono">{a.kind}({Object.entries(a.args).map(([k, v]) => `${k}=${v}`).join(", ")})</dd>
                      <dt>Proposed by</dt><dd className="mono">{a.proposed_by}</dd>
                      <dt>Approved by</dt><dd className="mono">{a.approved_by ?? "—"}</dd>
                      <dt>Safety net</dt><dd>{a.confirm_minutes != null ? <span className="mono">commit confirmed {a.confirm_minutes}</span> : "no commit made"} · auto-rollback if validation is silent</dd>
                    </dl>
                  </div>
                  <div className="card">
                    <h2>Guardrails checked before commit</h2>
                    {a.guardrails.length === 0 && <span className="small weak">No guardrail record.</span>}
                    {a.guardrails.map((g) => (
                      <div key={g.id} style={{ display: "flex", gap: 8, padding: "3px 0", fontSize: 13 }}>
                        <ToneIcon tone={g.passed ? "ok" : "crit"} size="15px" />
                        <span className="mono weak" style={{ minWidth: 26 }}>{g.id}</span>
                        <span>{g.name}{g.detail && <b className="tone-crit"> — {g.detail}</b>}</span>
                      </div>
                    ))}
                  </div>
                  <div className="card">
                    <h2>Change · {a.device}</h2>
                    {a.diff ? <Diff text={a.diff} /> : (
                      <>
                        <div className="small weak" style={{ marginBottom: 6 }}>Config diff not recorded by the executor. Commits on the device:</div>
                        {a.commits?.length ? (
                          <ul className="tl">
                            {a.commits.map((c, i) => (
                              <li key={i}>
                                <span className="mono weak">{time(c.ts)}</span>
                                <span className="mono">{c.comment ?? c.leg}</span>
                                <span className="small weak">{c.confirm_min ? `confirmed ${c.confirm_min} min` : ""}{c.seconds != null ? ` · ${c.seconds} s` : ""}</span>
                              </li>
                            ))}
                          </ul>
                        ) : <span className="small weak">No commit was made.</span>}
                      </>
                    )}
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                  <div className="card">
                    <h2>Validation timeline</h2>
                    <ul className="tl">
                      {a.validation.map((v, i) => (
                        <li key={i}>
                          <span className="mono weak">{time(v.t)}</span>
                          <span className="dot" style={{ background: DOT[v.level] ?? "#6F7B76" }} aria-hidden />
                          <span>{v.text}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  {a.series && a.series.length > 0 && (a.series_kind === "validation_checks" ? (
                    <div className="card">
                      <h2>Validation checks</h2>
                      <SleChart series={a.series} goal={100} height={110} label="Validation checks after the commit: 100 = check passed, 0 = check failed" />
                      <div className="small weak">{a.series.length} checks · 100 = passed, 0 = failed · {a.series.filter((p) => p.value === 0).length} failed</div>
                    </div>
                  ) : (
                    <div className="card">
                      <h2>Service level around the change</h2>
                      <SleChart series={a.series} goal={99} height={110} label="Success rate in the last 60 minutes around the change" />
                      <div className="small weak">Success rate, last 60 min · dashed line = goal</div>
                    </div>
                  ))}
                  {a.verifier && (
                    <div className={`notice ${a.verifier.status === "cleared" ? "ok" : "info"}`}>
                      <b>Independent verifier: {a.verifier.status}</b> — {a.verifier.detail}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </Load>
      </div>
      {confirm && a && (
        <Layer onEsc={() => setConfirm(null)} onClickOutside={() => setConfirm(null)} responsive={false} modal>
          <div style={{ padding: 22, width: 460, maxWidth: "calc(100vw - 40px)" }} role="dialog" aria-labelledby="dlg-h">
            <h1 id="dlg-h" style={{ fontSize: 17, marginBottom: 8 }}>{confirm === "approve" ? `Approve action ${a.id}?` : `Reject action ${a.id}?`}</h1>
            <p className="weak" style={{ marginTop: 0 }}>
              {confirm === "approve"
                ? <>The executor takes the {a.device} lease and commits with <span className="mono">commit confirmed{a.confirm_minutes != null ? ` ${a.confirm_minutes}` : ""}</span>. It rolls back by itself unless validation confirms it. You are approving as <b>{who.user}</b>.</>
                : "The proposal is rejected; nothing is sent to the device."}
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setConfirm(null)}>Back</button>
              <button className={`btn ${confirm === "approve" ? "primary" : "danger"}`} disabled={busy} onClick={() => run(confirm)}>
                {busy && <Busy />} {confirm === "approve" ? "Approve and commit" : "Reject"}
              </button>
            </div>
          </div>
        </Layer>
      )}
    </>
  );
}
