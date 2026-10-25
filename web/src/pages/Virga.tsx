// Fault lab: the operator's Virga page. Everyone can watch (catalogue, the live trial, history);
// only the operator's own session can inject, revert or recover, and the server decides that
// (docs/build/virga-live.md D2). This page never infers what may run: it shows what the API offers.
import Info from "../components/Info";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { api, ApiError, useApi } from "../api";
import TopBar from "../components/TopBar";
import { Loading, Pill, Seg } from "../components/ui";
import { ago } from "../format";
import { useSession } from "../session";
import {
  clock, isLive, PHASE_LABEL, secs, useServerNow, useVirga,
  type PhaseName, type VCatalogue, type VPhase, type VRecent, type VScenario, type VTrial,
} from "../virga";
import VirgaMark from "../components/VirgaMark";
import "../virga.css";

const FLOW: PhaseName[] = ["preflight", "inject", "verify_injected", "hold", "revert", "verify_reverted"];
const SITES: { id: string; label: string }[] = [{ id: "campus", label: "Campus" }, { id: "fabric", label: "Fabric" }];
const STATUS_WORD: Record<string, string> = { pending: "waiting", running: "running", ok: "done", failed: "failed", skipped: "skipped" };

const hms = (t?: number | null) => (t ? new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : "—");
const estimate = (s: number) => (s < 90 ? `about ${Math.round(s)} s` : `about ${Math.round(s / 60)} min`);

type Err = { status: number; title: string; detail: string };
const ERR_TITLE: Record<number, string> = {
  403: "Only the operator can do this",
  409: "Cannot do this right now",
  422: "That fault is not offered live",
  429: "Too many injects",
};
const toErr = (e: unknown): Err => {
  if (e instanceof ApiError) return { status: e.status, title: ERR_TITLE[e.status] ?? "Request failed", detail: e.message };
  return { status: 0, title: "Request failed", detail: e instanceof Error ? e.message : String(e) };
};

function ErrNotice({ e, onClose }: { e: Err; onClose: () => void }) {
  return (
    <div className="notice crit vg-err" role="alert">
      <span><b>{e.title}.</b> {e.detail}{e.status ? <span className="weak small"> (HTTP {e.status})</span> : null}</span>
      <button type="button" className="btn" onClick={onClose}>Dismiss</button>
    </div>
  );
}

function Step({ name, p, now, holdUntil }: { name: PhaseName; p?: VPhase; now: number; holdUntil: number | null }) {
  const status = p?.status ?? "pending";
  const dur = p?.t_start && p.t_end ? p.t_end - p.t_start : null;
  const holding = name === "hold" && status === "running" && holdUntil != null;
  const left = holding ? Math.max(0, holdUntil! - now) : 0;
  const span = holding && p?.t_start ? Math.max(1, holdUntil! - p.t_start) : 1;
  return (
    <li className={`vg-step s-${status}`} aria-current={status === "running" ? "step" : undefined}>
      <span className="vg-pin" aria-hidden><span /></span>
      <div className="vg-step-b">
        <div className="vg-step-h">
          <b>{PHASE_LABEL[name]}</b>
          <span className="sr-only"> ({STATUS_WORD[status] ?? status})</span>
          <span className="vg-step-t mono">{p?.t_start ? hms(p.t_start) : "—"}{dur != null ? ` · ${secs(dur)}` : ""}</span>
        </div>
        {holding && (
          <div className="vg-hold">
            <span className="vg-count mono" aria-hidden>{clock(Math.ceil(left))}</span>
            <span className="sr-only">Hold ends in {Math.ceil(left)} seconds</span>
            <span className="vg-bar" aria-hidden><i style={{ width: `${Math.min(100, Math.max(0, (1 - left / span) * 100))}%` }} /></span>
            <span className="small weak">left in the hold. Watch the lab react; it reverts when this reaches zero.</span>
          </div>
        )}
        {p?.detail ? <div className="small weak vg-detail">{p.detail}</div> : null}
      </div>
    </li>
  );
}

function RunnerLog({ log }: { log: { t: number; msg: string }[] }) {
  const ref = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [log]);
  return (
    <pre ref={ref} className="vg-log mono" role="log" aria-label="Runner log" tabIndex={0}
      onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24; }}>
      {log.length === 0 ? "(no log lines yet)" : log.slice(-50).map((l) => `${hms(l.t)}  ${l.msg}`).join("\n")}
    </pre>
  );
}

function Live({ t, simulated, operator, onError, reload }: { t: VTrial; simulated: boolean; operator: boolean; onError: (e: Err) => void; reload: () => void }) {
  const live = isLive(t);
  const now = useServerNow(live);
  const [busy, setBusy] = useState(false);
  const by = useMemo(() => new Map(t.phases.map((p) => [p.name, p])), [t.phases]);
  const holding = t.phase === "hold" && by.get("hold")?.status === "running";
  useEffect(() => { if (!holding) setBusy(false); }, [holding]);
  const revertNow = async () => {
    setBusy(true);
    try { await api(`/api/virga/trials/${encodeURIComponent(t.id)}/revert`, { method: "POST", json: {} }); reload(); }
    catch (e) { onError(toErr(e)); setBusy(false); }
  };
  const end = t.phases.reduce((m, p) => Math.max(m, p.t_end ?? 0), 0);
  return (
    <section className={`card vg-live${live ? " running" : ""}`} aria-labelledby="vg-live-h">
      <div className="vg-live-h">
        <h2 id="vg-live-h">{live ? "Live drill" : "Last drill"}</h2>
        <span className="mono">{t.id}</span>
        {live ? <Pill tone="warn">{PHASE_LABEL[t.phase]}</Pill> : null}
        {t.verdict === "pass" && <Pill tone="ok">Pass</Pill>}
        {t.verdict === "fail" && <Pill tone="crit">Fail</Pill>}
        {simulated && <span className="badge-demo">Simulated (canned demo data)</span>}
        <span className="spacer" />
        <span className="mono vg-elapsed" aria-hidden>{clock((live ? now : end || now) - t.t_start)}</span>
      </div>
      <div className="vg-live-t"><b>{t.scenario}</b> {t.title} <span className="weak">· {t.layer} · {t.site} · on {(t.nodes ?? []).join(", ")}</span></div>
      <div className="vg-live-grid">
        <ol className="vg-steps" aria-label="Drill phases">
          {FLOW.map((n) => <Step key={n} name={n} p={by.get(n)} now={now} holdUntil={t.hold_until} />)}
        </ol>
        <div className="vg-side">
          {t.verdict && (
            <div className={`notice ${t.verdict === "pass" ? "ok" : "crit"}`}>
              {t.verdict === "pass" ? <><b>Passed.</b> The fault took effect and the revert was verified.</> : <><b>Failed.</b> Read the runner log. If the lab was left changed, the operator must run Recover.</>}
            </div>
          )}
          {operator && holding && (
            <button type="button" className="btn primary" disabled={busy} onClick={() => void revertNow()}>{busy ? "Reverting…" : "Revert now"}</button>
          )}
          {live && <div className="vg-watch small">Watch it land: <Link to="/topology">Topology</Link> · <Link to="/theatre">Theatre</Link> · <Link to="/events">Events</Link> · <Link to="/sle">Service levels</Link></div>}
          {!live && <div className="vg-watch small">Afterwards: <Link to="/events">Events</Link> · <Link to="/root-cause">Root cause</Link> · <Link to="/sle">Service levels</Link></div>}
          <div className="k">Runner log</div>
          <RunnerLog log={t.log ?? []} />
        </div>
      </div>
    </section>
  );
}

function Card({ s, live, blocked, operator, hold, confirm, setConfirm, setHold, max, submitting, onGo }: {
  s: VScenario; live: boolean; blocked: string | null; operator: boolean; hold: number; max: number;
  confirm: boolean; setConfirm: (on: boolean) => void; setHold: (n: number) => void; submitting: boolean; onGo: () => void;
}) {
  const st = s.selftest;
  const sid = `vg-hold-${s.id}`;
  return (
    <li className={`vg-card${live ? " is-live" : ""}${!s.demo_ok ? " off" : ""}`}>
      <div className="vg-card-h">
        <span className="mono vg-id">{s.id}</span>
        <span className="vg-layer-chip">{s.layer}</span>
        <span style={{ flex: 1 }} />
        {s.impact === "latent" ? <Pill tone="info">No user impact</Pill> : <Pill tone="warn">User impact</Pill>}
      </div>
      <b className="vg-title">{s.title}</b>
      {s.demo_ok ? (
        <dl className="vg-meta">
          {(s.nodes ?? []).length > 0 && <><dt>Devices</dt><dd>{s.nodes.map((n) => <span key={n} className="vg-node mono">{n}</span>)}</dd></>}
          <dt>Self-test</dt><dd>{st.runs ? <span className={st.passes === st.runs ? "tone-ok" : "tone-warn"}>{st.passes}/{st.runs} clean{st.last_at ? <span className="weak"> · {ago(st.last_at)}</span> : null}</span> : "not self-tested"}</dd>
          <dt>Takes</dt><dd>{estimate(s.est_s)} + the hold</dd>
        </dl>
      ) : <div className="small vg-why">Not runnable here: {s.why_not ?? "no reason given"}</div>}
      {operator && s.demo_ok && !confirm && (
        <button type="button" className="btn primary vg-inject" disabled={!!blocked} title={blocked ?? undefined} onClick={() => setConfirm(true)}>Inject…</button>
      )}
      {operator && s.demo_ok && !confirm && blocked && <div className="small weak">{blocked}</div>}
      {confirm && (
        <div className="vg-confirm" role="group" aria-label={`Confirm injecting ${s.id}`}>
          <p>
            This makes a <b>real change</b> on <b>{(s.nodes ?? []).join(", ")}</b>.{" "}
            {s.impact === "user" ? "Traffic through those devices will be affected." : "It has no user impact."}{" "}
            It is a confirmed commit, so a device rolls itself back if the daemon dies mid-drill.
          </p>
          <label htmlFor={sid} className="vg-hold-l">Hold the fault for <b className="mono">{clock(hold)}</b></label>
          <input id={sid} type="range" min={30} max={Math.max(30, max)} step={10} value={Math.min(hold, max)} onChange={(e) => setHold(Number(e.target.value))} />
          <div className="vg-confirm-a">
            <button type="button" className="btn primary" disabled={submitting} onClick={onGo}>{submitting ? "Starting…" : `Inject ${s.id} on ${(s.nodes ?? []).join(", ")}`}</button>
            <button type="button" className="btn" disabled={submitting} onClick={() => setConfirm(false)}>Cancel</button>
          </div>
        </div>
      )}
    </li>
  );
}

function History({ rows }: { rows: VRecent[] }) {
  if (!rows.length) return <div className="small weak" style={{ padding: "12px 16px" }}>No drills recorded yet.</div>;
  return (
    <table className="t vg-hist">
      <thead><tr><th>Drill</th><th>Started</th><th>Verdict</th><th className="num">Took effect after</th><th className="num">Restored after</th><th>Incident</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td data-label="Drill"><span className="mono">{r.scenario}</span> {r.title}</td>
            <td data-label="Started" title={new Date(r.t_start * 1000).toLocaleString()}>{ago(r.t_start)}</td>
            <td data-label="Verdict">{r.verdict === "pass" ? <Pill tone="ok">Pass</Pill> : r.verdict === "fail" ? <Pill tone="crit">Fail</Pill> : <Pill tone="neutral">{r.t_end ? "No verdict" : "Running"}</Pill>}</td>
            <td data-label="Took effect after" className="num">{secs(r.time_to_effect_s)}</td>
            <td data-label="Restored after" className="num">{secs(r.time_to_restore_s)}</td>
            <td data-label="Incident">{r.incident_opened ? <Link to="/root-cause">opened</Link> : <span className="weak">none</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Virga() {
  const { who } = useSession();
  const { state, conn, reload } = useVirga();
  const cat = useApi<VCatalogue>("/api/virga/catalogue");
  const operator = who.role === "operator" && !who.replay;
  const [err, setErr] = useState<Err | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [hold, setHold] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [show, setShow] = useState<"live" | "all">("live");
  const [site, setSite] = useState<string>("all");
  const [imp, setImp] = useState<"all" | "user" | "latent">("all");
  const [q, setQ] = useState("");
  const [rec, setRec] = useState<{ busy: boolean; msg: string | null }>({ busy: false, msg: null });
  const liveRef = useRef<HTMLDivElement>(null);
  const scrollTo = useRef<string | null>(null);

  const c = cat.data;
  const holdS = hold ?? c?.hold.default_s ?? 180;
  const maxS = c?.hold.max_s ?? 600;
  const trial = state?.trial ?? null;
  const live = isLive(trial);
  const enabled = state?.enabled ?? c?.enabled ?? false;
  const reason = state?.reason ?? c?.reason ?? null;
  const simulated = state?.simulated ?? c?.simulated ?? false;
  const blocked = !enabled ? "The fault lab is off." : state?.needs_recovery ? "Recover the lab first." : state?.busy || live ? "A drill is already running." : null;

  useEffect(() => { // bring a drill you just started into view
    if (trial && scrollTo.current && trial.id === scrollTo.current) {
      scrollTo.current = null;
      liveRef.current?.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
  }, [trial]);

  const inject = async (s: VScenario) => {
    setSubmitting(true); setErr(null);
    try {
      const r = await api<{ trial_id: string }>("/api/virga/trials", { json: { scenario: s.id, hold_s: Math.min(holdS, maxS) } });
      scrollTo.current = r.trial_id;
      setConfirm(null);
      reload();
    } catch (e) { setErr(toErr(e)); setConfirm(null); }
    finally { setSubmitting(false); }
  };
  const recover = async () => {
    setRec({ busy: true, msg: null }); setErr(null);
    try {
      const r = await api<{ recovered: string[]; ok: boolean }>("/api/virga/recover", { json: {} });
      setRec({ busy: false, msg: r.ok ? (r.recovered.length ? `Recovered ${r.recovered.join(", ")}.` : "Nothing needed recovering.") : `Recovery did not finish${r.recovered.length ? ` (recovered ${r.recovered.join(", ")})` : ""}. Check the devices before another drill.` });
      reload();
    } catch (e) { setErr(toErr(e)); setRec({ busy: false, msg: null }); }
  };

  const groups = useMemo(() => {
    const out: { site: string; label: string; layers: { layer: string; items: VScenario[] }[] }[] = [];
    const known = new Set(SITES.map((s) => s.id));
    const sites = [...SITES, ...[...new Set((c?.scenarios ?? []).map((s) => s.site))].filter((s) => !known.has(s)).map((s) => ({ id: s, label: s }))];
    for (const st of sites) {
      const needle = q.trim().toLowerCase();
      const mine = (c?.scenarios ?? []).filter((s) => s.site === st.id && (site === "all" || s.site === site) && (show === "all" || s.demo_ok)
        && (imp === "all" || s.impact === imp)
        && (!needle || `${s.id} ${s.title} ${s.layer} ${(s.nodes ?? []).join(" ")}`.toLowerCase().includes(needle)));
      if (!mine.length) continue;
      // runnable faults first, then by layer
      mine.sort((a, b) => Number(b.demo_ok) - Number(a.demo_ok) || a.layer.localeCompare(b.layer) || a.id.localeCompare(b.id));
      out.push({ site: st.id, label: st.label, layers: [{ layer: "", items: mine }] });
    }
    return out;
  }, [c, show, site, imp, q]);

  const unavailable = conn === "off" && !state && (cat.error as ApiError | undefined)?.status === 404;

  return (
    <>
      <TopBar title={<span className="vg-title"><VirgaMark size={22} /> Virga</span>} crumbs="Fault lab · inject a real fault, watch the lab detect it, then revert" />
      <div className="content vg-page">
        {unavailable && <div className="notice info"><b>The fault lab is not available on this server.</b> It is a feature of the live lab host.</div>}
        {!unavailable && conn === "connecting" && !state && <Loading label="Connecting to the fault lab" rows={2} />}
        {simulated && (
          <div className="notice warn" role="status"><b>Simulated (canned demo data).</b> No device is contacted and nothing is injected.</div>
        )}
        {state && !enabled && (
          <div className="notice info" role="status"><b>The fault lab is off.</b> {reason ?? "Live injection is not enabled on this server."} The catalogue and history are still shown.</div>
        )}
        {state?.needs_recovery && (
          <div className="notice crit vg-recover" role="alert">
            <span><b>A previous drill did not finish.</b> The lab may still hold an injected change. New drills are refused until it is recovered.</span>
            {operator
              ? <button type="button" className="btn primary" disabled={rec.busy} onClick={() => void recover()}>{rec.busy ? "Recovering…" : "Recover"}</button>
              : <span className="small">Only the operator can run recovery.</span>}
          </div>
        )}
        {rec.msg && <div className="notice info" role="status">{rec.msg}</div>}
        {err && <ErrNotice e={err} onClose={() => setErr(null)} />}
        <section className="vg-hero" aria-label="About Virga">
          <div className="vg-hero-t">
            <VirgaMark size={36} />
            <div>
              <b>Virga</b> <span className="weak">· the fault injector</span>
              <p>Virga breaks the lab on purpose with realistic faults, then checks whether Shimpaku noticed, explained and fixed them.
                Like the rain it is named after, every fault is meant to vanish: each drill ends by reverting it and checking the revert took.</p>
              <ol className="vg-how" aria-label="How a drill runs">
                {FLOW.map((n, i) => <li key={n}><span>{i + 1}</span>{PHASE_LABEL[n]}</li>)}
              </ol>
            </div>
          </div>
          <dl className="vg-stats">
            <div><dt>In the catalogue</dt><dd>{c ? c.scenarios.length : "—"}</dd></div>
            <div><dt>Runnable live<Info k="vg_live" /></dt><dd>{c ? c.scenarios.filter((x) => x.demo_ok).length : "—"}</dd></div>
            <div><dt>Recent drills</dt><dd>{state ? state.recent.length : "—"}</dd></div>
            <div><dt>Last drill</dt><dd>{state?.recent[0] ? <>{state.recent[0].verdict === "pass" ? <span className="tone-ok">Pass</span> : state.recent[0].verdict === "fail" ? <span className="tone-crit">Fail</span> : "—"} <small>{ago(state.recent[0].t_start)}</small></> : "none yet"}</dd></div>
          </dl>
        </section>

        <div ref={liveRef} className="vg-anchor">
          {trial
            ? <Live t={trial} simulated={simulated} operator={operator} onError={setErr} reload={reload} />
            : state && enabled && (
              <div className="card vg-idle">
                <span className="vg-idle-dot" aria-hidden />
                <div><b>No drill running.</b> <span className="weak">{operator ? "Pick a runnable fault below and press Inject." : "When the operator injects a fault it appears here, step by step, live."}</span></div>
                {!operator && !who.replay && <Link className="btn" to="/login">Operator sign-in</Link>}
              </div>
            )}
        </div>

        {state && state.recent.length > 0 && (
          <section className="card flush" aria-labelledby="vg-hist-h">
            <div className="card-h"><h2 id="vg-hist-h">Recent drills</h2><span className="small weak">last {state.recent.length} {state.recent.length === 1 ? "drill" : "drills"}</span></div>
            <History rows={state.recent} />
          </section>
        )}

        {!unavailable && <section aria-labelledby="vg-cat-h">
          <div className="vg-cat-h">
            <h2 id="vg-cat-h">Fault catalogue<Info k="virga" /></h2>
            {c && (
              <div className="vg-tools" role="group" aria-label="Filter the catalogue">
                <Seg label="Which faults" value={show} onChange={setShow}
                  options={[{ value: "live", label: `Runnable live (${c.scenarios.filter((x) => x.demo_ok).length})` }, { value: "all", label: `All (${c.scenarios.length})` }]} />
                <Seg label="Site" value={site} onChange={setSite} options={[{ value: "all", label: "Both sites" }, ...SITES.map((x) => ({ value: x.id, label: x.label }))]} />
                <Seg label="Impact" value={imp} onChange={setImp} options={[{ value: "all", label: "Any impact" }, { value: "user", label: "User impact" }, { value: "latent", label: "Latent" }]} />
                <Info k="vg_impact" />
                <input className="vg-search" type="search" placeholder="Search faults or devices…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the catalogue" />
              </div>
            )}
          </div>
          {cat.loading && !c && <Loading rows={3} />}
          {cat.error && !c && !unavailable && <div className="notice crit" role="alert">Could not load the catalogue: {cat.error.message}</div>}
          {c && groups.length === 0 && <div className="card small weak">No fault matches these filters. <button type="button" className="linkish" onClick={() => { setShow("all"); setSite("all"); setImp("all"); setQ(""); }}>Show everything</button></div>}
          {groups.map((g) => (
            <div key={g.site} className="vg-site">
              <h3 className="vg-site-h">{g.label} <span className="weak small">· {g.layers.reduce((n, l) => n + l.items.length, 0)}</span></h3>
              <ul className="vg-grid">
              {g.layers.map((l) => (
                    l.items.map((s) => (
                      <Card key={s.id} s={s} live={live && trial?.scenario === s.id} blocked={blocked} operator={operator}
                        hold={holdS} max={maxS} setHold={setHold}
                        confirm={confirm === s.id} setConfirm={(on) => setConfirm(on ? s.id : null)}
                        submitting={submitting} onGo={() => void inject(s)} />
                    ))
              ))}
              </ul>
            </div>
          ))}
        </section>}

      </div>
    </>
  );
}
