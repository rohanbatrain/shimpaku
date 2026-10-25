// Theatre mode: one screen that follows the break-and-fix demo as it happens (or replays the last
// completed run). Every stage shown is a stored event with its stored timestamp (/api/theatre);
// nothing is estimated here. The only things this page computes are durations between two
// recorded times, the countdown to the action's recorded validation end, and (in a replay) which
// recorded events have "happened yet" on the replay clock.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, ask, useApi, type ApiError } from "../api";
import TopBar from "../components/TopBar";
import { Busy, Load } from "../components/ui";
import { useLease } from "../components/Lease";
import { fixtureArgs } from "../components/fixture";
import { useSession } from "../session";
import "../theatre.css";

type Stage = {
  id: string; label: string; t: number | null; state: "done" | "current" | "pending" | "skipped" | "failed"; detail: string | null;
  by?: { name: string; kind?: string | null; role?: string | null }; checks?: { id: string; passed: boolean; name: string; detail: string | null }[] | number;
  junos_time?: string | null; starts?: number; ends?: number | null; checks_ok?: number; refault_t?: number;
};
type Run = {
  mode: "live" | "replay" | "pending" | "none"; demo?: boolean; note?: string;
  action: { id: string; status: string; proposed_by: { name: string; kind: string | null; role: string | null } } | null;
  started_at: number | null; ended_at: number | null; stages: Stage[];
  fixture: { present: boolean | null; reason: string | null; live: boolean } | null; server_now: number;
};

export const PROPOSE_PROMPT = "Propose the fix: VLAN 999 is missing from the sw1 ge-0/0/5 demo trunk. Propose add_vlan_to_trunk for sw1 ge-0/0/5 vlan 999.";

function hms(t?: number | null) {
  if (!t) return "—";
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}
export function dur(s: number) {
  if (s < 0) s = 0;
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 90) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  return r ? `${m} min ${r} s` : `${m} min`;
}
function clock(s: number) {
  s = Math.max(0, Math.ceil(s));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
// what each stage means, for someone new to networks (shown under each stage)
const STAGE_HELP: Record<string, string> = {
  break: "Someone breaks the lab on purpose: here VLAN 999 is removed from one switch port.",
  dip: "The first minute a service score drops below its goal: the first sign anyone would notice.",
  incident: "The system groups the bad minutes into an incident with a likely cause.",
  propose: "A fix is suggested (by Shim, an outside agent or a person). Nothing changes yet.",
  guardrails: "Ten automatic safety checks run on the proposal. Any failure blocks it.",
  approve: "A signed-in human reads the proposal and approves it. AI can never do this step.",
  commit: "The change is applied with 'commit confirmed': the device undoes it by itself unless confirmed.",
  health: "A fresh check that the device is still healthy after the change.",
  confirm: "Healthy, so the change is confirmed and kept.",
  validate: "A few minutes of watching to be sure the problem stays fixed.",
  outcome: "Verified (fixed and stayed fixed) or Recurred (the problem came back).",
};

// words for the gap into a stage, from the stage before it that has a time
const GAP_WORD: Record<string, string> = {
  dip: "first dip", incident: "opened", propose: "proposed", guardrails: "checked", approve: "approved",
  commit: "committed", health: "checked", confirm: "confirmed", validate: "window opened", outcome: "",
};

/** Seconds since the latest earlier stage (in list order) recorded at or before this one. */
function gapOf(stages: Stage[], i: number, shown: (s: Stage) => boolean): number | null {
  const t = stages[i].t;
  if (!t || !shown(stages[i])) return null;
  const prior = stages.slice(0, i).filter((x) => shown(x) && x.t != null && (x.t as number) <= t).map((x) => x.t as number);
  return prior.length ? t - Math.max(...prior) : null;
}

function useReduced() {
  const [r, setR] = useState(() => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    const m = window.matchMedia("(prefers-reduced-motion: reduce)");
    const h = () => setR(m.matches);
    m.addEventListener("change", h);
    return () => m.removeEventListener("change", h);
  }, []);
  return r;
}

/** Replay clock: walks the recorded times in order. Gaps up to 12 s play at real speed; longer
 * gaps (the validation window) are fast-forwarded to ~4 s and labelled as such. */
function useReplay(run: Run | undefined, auto: boolean) {
  const times = useMemo(() => (run ? run.stages.map((s) => s.t).filter((t): t is number => !!t).sort((a, b) => a - b) : []), [run]);
  const [t, setT] = useState<number | null>(null);
  const [ff, setFf] = useState(false);
  const timer = useRef<number | null>(null);
  const stop = useCallback(() => { if (timer.current) cancelAnimationFrame(timer.current); timer.current = null; setT(null); setFf(false); }, []);
  const play = useCallback(() => {
    if (!times.length) return;
    if (timer.current) cancelAnimationFrame(timer.current);
    const start = times[0] - 1.5, end = times[times.length - 1];
    let cur = start, last = performance.now();
    const step = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      const next = times.find((x) => x > cur) ?? end;
      const gap = next - (times.filter((x) => x <= cur).pop() ?? start);
      const fast = gap > 12;
      setFf(fast);
      cur += fast ? dt * (gap / 4) : dt;
      if (cur >= end) { setT(null); setFf(false); timer.current = null; return; }
      setT(cur);
      timer.current = requestAnimationFrame(step);
    };
    setT(start);
    timer.current = requestAnimationFrame(step);
  }, [times]);
  const autoed = useRef<string | null>(null);
  useEffect(() => {
    const id = run?.action?.id ?? null;
    if (auto && run?.mode === "replay" && id && autoed.current !== id) { autoed.current = id; play(); }
  }, [auto, run, play]);
  useEffect(() => () => { if (timer.current) cancelAnimationFrame(timer.current); }, []);
  return { t, ff, play, stop, playing: t != null && timer.current != null };
}

function vlanPresent(run: Run, at: number | null): boolean | null {
  // live: the fixture port as read from sw1 (cached ≤ 5 s); replay: the run's recorded events
  if (at == null && run.fixture?.live && run.fixture.present != null) return run.fixture.present;
  const T = at ?? Infinity;
  const s = Object.fromEntries(run.stages.map((x) => [x.id, x]));
  let p: boolean | null = run.mode === "none" ? null : true;
  const ev: [number, boolean][] = [];
  if (s.break?.t) ev.push([s.break.t, false]);
  if (s.commit?.t) ev.push([s.commit.t, true]);
  if (s.outcome?.refault_t) ev.push([s.outcome.refault_t, false]);
  for (const [t, v] of ev.sort((a, b) => a[0] - b[0])) if (t <= T) p = v;
  return p;
}

function MiniTopo({ present, label }: { present: boolean | null; label: string }) {
  const c = present == null ? "var(--th-unknown)" : present ? "var(--th-ok)" : "var(--th-bad)";
  const node = (x: number, y: number, name: string, role: string) => (
    <g transform={`translate(${x} ${y})`}>
      <circle r="30" className="th-node" />
      <text y="6" textAnchor="middle" className="th-node-t">{name}</text>
      <text y="52" textAnchor="middle" className="th-node-r">{role}</text>
    </g>
  );
  return (
    <figure className="th-topo" aria-label={`Campus topology. Demo trunk sw1 ge-0/0/5: VLAN 999 ${present == null ? "unknown" : present ? "present" : "missing"}`}>
      <svg viewBox="0 0 520 330" role="img" aria-hidden>
        <line x1="110" y1="80" x2="330" y2="80" className="th-link" />
        <line x1="110" y1="80" x2="110" y2="240" className="th-link" />
        <text x="220" y="70" textAnchor="middle" className="th-if">routed</text>
        <text x="122" y="165" className="th-if">trunk</text>
        <line x1="140" y1="240" x2="300" y2="240" className={`th-trunk${present === false ? " bad" : ""}`} style={{ stroke: c }} />
        {node(110, 80, "r1", "router")}
        {node(330, 80, "r2", "router")}
        {node(110, 240, "sw1", "switch")}
        <g transform="translate(300 202)">
          <rect width="200" height="76" rx="12" className="th-fix" style={{ stroke: c }} />
          <text x="16" y="26" className="th-fix-t">ge-0/0/5 · demo trunk</text>
          <g transform="translate(16 40)">
            <rect width="74" height="24" rx="12" className="th-vlan ok" />
            <text x="37" y="17" textAnchor="middle" className="th-vlan-t">998 ✓</text>
            <g transform="translate(84 0)" className={`th-vlan999 ${present === false ? "bad" : present ? "ok" : "unk"}`}>
              <rect width="86" height="24" rx="12" className="th-vlan" />
              <text x="43" y="17" textAnchor="middle" className="th-vlan-t">999 {present == null ? "?" : present ? "✓" : "✗"}</text>
            </g>
          </g>
        </g>
      </svg>
      <figcaption>
        <span className={`th-dot ${present === false ? "bad" : present ? "ok" : ""}`} aria-hidden />
        VLAN 999 <b>{present == null ? "unknown" : present ? "present" : "missing"}</b> on sw1 ge-0/0/5 · <span className="th-dim">{label}</span>
      </figcaption>
    </figure>
  );
}

function Timeline({ stages, at, reduced }: { stages: Stage[]; at: number | null; reduced: boolean }) {
  const ref = useRef<HTMLOListElement>(null);
  const cur = stages.findIndex((s) => stateAt(s, at) === "current");
  useEffect(() => {
    const el = ref.current?.children[cur] as HTMLElement | undefined;
    if (!el || !ref.current) return;
    const box = ref.current;
    const left = el.offsetLeft - box.clientWidth / 2 + el.clientWidth / 2;
    box.scrollTo({ left, behavior: reduced ? "auto" : "smooth" });
  }, [cur, reduced]);
  const shown = (x: Stage) => ["done", "failed"].includes(stateAt(x, at));
  return (
    <ol className="th-line" ref={ref} aria-label="Run timeline">
      {stages.map((s, i) => {
        const st = stateAt(s, at);
        const t = shown(s) ? s.t : null;
        const gap = gapOf(stages, i, shown);
        return (
          <li key={s.id} className={`th-step s-${st}`} aria-current={st === "current" ? "step" : undefined}>
            <span className="th-gap">{gap != null ? `+${dur(gap)}` : " "}</span>
            <span className="th-pin" aria-hidden><span /></span>
            <b>{s.id === "outcome" && st !== "done" && st !== "failed" ? "Verified / Recurred" : s.label}</b>
            <span className="th-t mono">{t ? hms(t) : st === "skipped" ? "none recorded" : st === "current" ? "now" : "—"}</span>
          </li>
        );
      })}
    </ol>
  );
}

// in a replay, a recorded stage "has not happened yet" until the replay clock reaches it
function stateAt(s: Stage, at: number | null): Stage["state"] {
  if (at == null) return s.state;
  if (s.state === "skipped") return "skipped";
  if (s.t && s.t <= at) return s.id === "validate" && s.ends && at < s.ends - 0.01 && s.state === "done" ? "current" : s.state;
  return s.state === "pending" ? "pending" : "pending";
}

function Countdown({ s, now }: { s: Stage; now: number }) {
  if (!s.starts || !s.ends) return null;
  const total = s.ends - s.starts, left = Math.max(0, s.ends - now);
  const frac = total > 0 ? 1 - left / total : 1;
  const R = 52, C = 2 * Math.PI * R;
  return (
    <div className="th-count" role="timer" aria-label={`Validation window: ${clock(left)} left of ${dur(total)}`}>
      <svg viewBox="0 0 120 120" aria-hidden>
        <circle cx="60" cy="60" r={R} className="th-ring-bg" />
        <circle cx="60" cy="60" r={R} className="th-ring" strokeDasharray={C} strokeDashoffset={C * (1 - frac)} />
      </svg>
      <div>
        <div className="th-count-n mono">{clock(left)}</div>
        <div className="th-dim">left of the {dur(total)} validation window · ends {hms(s.ends)}</div>
        {typeof s.checks === "number" && s.checks > 0 && <div className="th-dim">{s.checks_ok ?? 0} of {s.checks} checks passed so far</div>}
      </div>
    </div>
  );
}

function StepList({ stages, at, now }: { stages: Stage[]; at: number | null; now: number }) {
  const s = Object.fromEntries(stages.map((x) => [x.id, x]));
  const isShown = (y: Stage) => ["done", "failed"].includes(stateAt(y, at));
  return (
    <ol className="th-list" aria-live="polite">
      {stages.map((x, i) => {
        const st = stateAt(x, at);
        const shown = isShown(x);
        const gap = x.id === "guardrails" ? null : gapOf(stages, i, isShown);
        const fromBreak = shown && x.t && s.break?.t && x.id !== "break" ? x.t - s.break.t : null;
        const label = x.id === "outcome" && !shown ? "Verified or recurred" : x.label;
        return (
          <li key={x.id} className={`th-row s-${st}`}>
            <span className="th-ico" aria-hidden>{st === "done" ? "✓" : st === "failed" ? "✗" : st === "skipped" ? "–" : st === "current" ? "" : ""}</span>
            <div className="th-row-b">
              <div className="th-row-h">
                <b>{label}</b>
                {gap != null && GAP_WORD[x.id] !== undefined && (
                  <span className="th-chip">{x.id === "outcome" ? `after ${dur(gap)}` : `${GAP_WORD[x.id]} in ${dur(gap)}`}</span>
                )}
                <span className="th-row-t mono">{shown ? hms(x.t) : st === "current" ? "in progress" : st === "skipped" ? "" : "waiting"}</span>
              </div>
              {STAGE_HELP[x.id] && <div className="th-row-help">{STAGE_HELP[x.id]}</div>}
              {(shown || st === "skipped") && x.detail && <div className="th-row-d">{x.detail}</div>}
              {shown && x.by && <div className="th-row-d">by <b className="mono">{x.by.name}</b>{x.by.kind ? ` · ${x.by.kind}` : ""}{x.by.role ? ` (${x.by.role})` : ""}</div>}
              {shown && x.junos_time && <div className="th-row-d">Junos commit time <span className="mono">{x.junos_time}</span></div>}
              {shown && x.checks && Array.isArray(x.checks) && (
                <div className="th-guards" aria-label="Guardrails">
                  {x.checks.map((g, i) => (
                    <span key={g.id} className={`th-g ${g.passed ? "ok" : "bad"}`} style={{ animationDelay: `${i * 70}ms` }} title={g.detail ?? g.name}>
                      {g.passed ? "✓" : "✗"} {g.id}
                    </span>
                  ))}
                </div>
              )}
              {x.id === "outcome" && shown && fromBreak != null && s.confirm?.t && (
                <div className="th-row-d">fault to confirmed fix: <b>{dur(s.confirm.t - (s.break.t as number))}</b></div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Controls({ run, reload }: { run: Run; reload: () => void }) {
  const { who } = useSession();
  const tester = who.role === "tester";
  const lease = useLease(tester);
  const active = !tester || lease.data?.lease?.state === "active";
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [fallback, setFallback] = useState(false);
  const [sure, setSure] = useState(false);
  const st = run.action?.status;
  const live = run.mode === "live";
  const go = async (label: string, f: () => Promise<string>) => {
    setBusy(label); setMsg(null);
    try { setMsg({ tone: "ok", text: await f() }); } catch (e: any) { setMsg({ tone: "crit", text: (e as ApiError).message }); }
    finally { setBusy(null); reload(); lease.reload(); }
  };
  const brk = () => go("break", async () => {
    const r = await api("/api/demo/break", { method: "POST" });
    return r.changed ? "Fault injected: VLAN 999 removed from the trunk." : "Already broken: nothing changed.";
  });
  const viaShim = () => go("propose", async () => {
    let tools = 0, err = "";
    await ask(PROPOSE_PROMPT, { page: "theatre" }, (e) => {
      if (e.type === "tool") { tools += 1; setMsg({ tone: "info", text: `Shim is working: ${e.name}…` }); }
      if (e.type === "error") err = e.message;
    });
    setFallback(true);
    if (err) throw new Error(`Shim: ${err}`);
    return `Shim finished (${tools} tool calls). If no proposal appears, propose directly.`;
  });
  const direct = () => go("direct", async () => {
    const r = await api("/api/actions", { json: { kind: "add_vlan_to_trunk", args: fixtureArgs, reason: "restore VLAN 999 on the demo trunk (theatre)" } });
    return `Proposed ${r.id}: ${String(r.status).replace(/_/g, " ")}.`;
  });
  const approve = () => go("approve", async () => {
    setSure(false);
    await api(`/api/actions/${run.action!.id}/approve`, { method: "POST" });
    return `Approved as ${who.user}. The executor takes the sw1 lease and commits.`;
  });
  const reject = () => go("reject", async () => {
    setSure(false);
    await api(`/api/actions/${run.action!.id}/reject`, { method: "POST" });
    return "Rejected. Nothing was sent to the device.";
  });
  return (
    <>
    {st === "awaiting_approval" && run.action && (
      <ApprovalCard run={run} canApprove={who.can_approve && active} user={who.user} busy={busy} sure={sure} setSure={setSure} approve={approve} reject={reject} />
    )}
    <div className="th-ctl" role="group" aria-label="Presenter controls">
      <button className="th-btn bad" disabled={!active || !!busy || live} onClick={brk} title={live ? "A run is in progress" : "A real Junos commit on the lab"}>{busy === "break" && <Busy />} Break VLAN 999</button>
      <button className="th-btn" disabled={!active || !!busy || run.mode !== "pending"} onClick={viaShim} title="Ask Shim (the built-in agent) to propose; it cannot approve">{busy === "propose" && <Busy />} Propose via Shim</button>
      {(fallback || (msg?.tone === "crit" && busy == null && run.mode === "pending")) && run.mode === "pending" && (
        <button className="th-btn" disabled={!active || !!busy} onClick={direct}>{busy === "direct" && <Busy />} Propose directly</button>
      )}
      {msg && <span className={`th-msg ${msg.tone}`} role="status">{msg.text}</span>}
    </div>
    </>
  );
}

/** The decision a human must take, laid out so anyone can read it: what changes, who asked, what the
 * safety checks said, what happens next. Only a signed-in operator gets the buttons. */
function ApprovalCard({ run, canApprove, user, busy, sure, setSure, approve, reject }: {
  run: Run; canApprove: boolean; user: string | null; busy: string | null; sure: boolean;
  setSure: (b: boolean) => void; approve: () => void; reject: () => void;
}) {
  const g = run.stages.find((x) => x.id === "guardrails");
  const checks = Array.isArray(g?.checks) ? g!.checks : [];
  const failed = checks.filter((c) => !c.passed);
  const by = run.action!.proposed_by;
  return (
    <section className="th-approve" aria-label="Approval needed">
      <div className="th-approve-h">
        <span className="th-approve-badge">Approval needed</span>
        <span className="th-dim">action <Link to={`/actions/${run.action!.id}`} className="mono">{run.action!.id}</Link></span>
      </div>
      <dl className="th-approve-kv">
        <div><dt>The change</dt><dd>Add VLAN 999 back to the trunk port <b className="mono">ge-0/0/5</b> on switch <b className="mono">sw1</b></dd></div>
        <div><dt>Proposed by</dt><dd><b className="mono">{by.name}</b>{by.kind ? ` · ${by.kind === "assistant" ? "AI assistant" : by.kind}` : ""}</dd></div>
        <div><dt>Safety checks</dt><dd>{checks.length
          ? failed.length ? <span className="th-bad-t">{failed.length} of {checks.length} failed: {failed.map((c) => c.id).join(", ")}</span>
            : <span className="th-ok-t">all {checks.length} passed</span>
          : "not recorded"}</dd></div>
        <div><dt>If approved</dt><dd>sw1 is locked for this change, the commit is applied with an automatic undo timer, the device's health is re-checked, then the change is confirmed and watched for a few minutes.</dd></div>
      </dl>
      {canApprove ? (sure ? (
        <div className="th-approve-go">
          <span>Approve and commit to <b className="mono">sw1</b> as <b>{user}</b>?</span>
          <button className="th-btn ok" disabled={!!busy} onClick={approve}>{busy === "approve" && <Busy />} Yes, approve and commit</button>
          <button className="th-btn ghost" onClick={() => setSure(false)}>Back</button>
        </div>
      ) : (
        <div className="th-approve-go">
          <button className="th-btn ok" disabled={!!busy || failed.length > 0} onClick={() => setSure(true)}>Approve…</button>
          <button className="th-btn bad" disabled={!!busy} onClick={reject}>{busy === "reject" && <Busy />} Reject</button>
          {failed.length > 0 && <span className="th-dim">A failed safety check blocks approval.</span>}
        </div>
      )) : (
        <div className="th-dim">Waiting for a signed-in operator to approve or reject. AI agents can propose, never approve.</div>
      )}
    </section>
  );
}

export default function Theatre() {
  const [sp, setSp] = useSearchParams();
  const action = sp.get("action");
  const kiosk = sp.get("kiosk") === "1";
  const qs = action ? `?action=${encodeURIComponent(action)}` : "";
  const q = useApi<Run>(`/api/theatre${qs}`, { url: `/api/stream/theatre${qs}`, event: "theatre" });
  const runs = useApi<{ runs: { id: string; status: string; created_at: number }[] }>("/api/theatre/runs");
  const { who } = useSession();
  const reduced = useReduced();
  const visitor = who.role === "visitor" || !!who.replay;
  const canDrive = !who.replay && ((who.role === "operator" && who.kind === "session") || who.role === "tester");
  const run = q.data;
  const replay = useReplay(run, visitor || sp.get("replay") === "1");
  const [offset, setOffset] = useState(0);
  useEffect(() => { if (run?.server_now) setOffset(run.server_now - Date.now() / 1000); }, [run?.server_now]);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000 + offset), 250); return () => clearInterval(t); }, [offset]);
  // the run picker: refetched whenever the followed run changes
  const reloadRuns = runs.reload;
  useEffect(() => { reloadRuns(); }, [run?.action?.id, reloadRuns]);
  const box = useRef<HTMLDivElement>(null);
  const full = () => { void (document.fullscreenElement ? document.exitFullscreen() : box.current?.requestFullscreen())?.catch(() => {}); };
  const at = replay.t;

  const head = run && (
    run.mode === "live" ? <span className="th-mode live"><span className="th-live" aria-hidden />Live</span>
      : run.mode === "pending" ? <span className="th-mode live"><span className="th-live" aria-hidden />Live · fault injected</span>
      : run.mode === "replay" ? <span className="th-mode">{at != null ? (replay.ff ? "Replay · fast-forwarding" : "Replay · real timings") : "Completed run"}</span>
      : <span className="th-mode">No run yet</span>
  );
  const outcome = run?.stages.find((s) => s.id === "outcome");
  const validating = run?.stages.find((s) => s.id === "validate" && stateAt(s, at) === "current" && s.starts && s.ends) ?? null;
  const final = run && at == null && outcome && (outcome.state === "done" || outcome.state === "failed") ? outcome : null;

  return (
    <>
      {!kiosk && (
        <TopBar title="Theatre" crumbs={<><Link to="/actions">Actions</Link> / break and fix, as it happens</>}>
          {runs.data && runs.data.runs.length > 0 && (
            <label className="th-pick">
              <span className="sr-only">Run</span>
              <select value={action ?? ""} onChange={(e) => setSp(e.target.value ? { action: e.target.value } : {})}>
                <option value="">{visitor ? "Latest completed run" : "Latest run"}</option>
                {runs.data.runs.map((r) => <option key={r.id} value={r.id}>{r.id} · {r.status.replace(/_/g, " ")}</option>)}
              </select>
            </label>
          )}
        </TopBar>
      )}
      <div className={`theatre${kiosk ? " kiosk" : ""}`} ref={box}>
        <Load q={q} rows={6}>
          {(run) => (
            <>
              <div className="th-head">
                <div>
                  <div className="th-kicker">Break and fix · VLAN 999 on sw1 ge-0/0/5{run.demo ? " · demo data" : ""}</div>
                  <h2 className="th-title">
                    {run.action ? <>Action <Link to={`/actions/${run.action.id}`} className="mono">{run.action.id}</Link></> : run.mode === "pending" ? "Waiting for a proposal" : "No run recorded yet"}
                    {head}
                  </h2>
                </div>
                <div className="th-head-r">
                  {run.mode === "replay" && (at == null
                    ? <button className="th-btn" onClick={replay.play}>▶ Replay with real timings</button>
                    : <button className="th-btn ghost" onClick={replay.stop}>Skip to the end</button>)}
                  {typeof document !== "undefined" && document.fullscreenEnabled && <button className="th-btn ghost th-full" onClick={full} aria-label="Full screen" title="Full screen">⛶</button>}
                </div>
              </div>
              <Timeline stages={run.stages} at={at} reduced={reduced} />
              {canDrive && <Controls run={run} reload={q.reload} />}
              {visitor && <p className="th-ro">Read-only: {run.mode === "replay" ? "a replay of the most recent completed run, with its recorded times." : "watching."} <a href="/request">Request a live session</a> to run it yourself.</p>}
              <div className="th-body">
                <div className="th-left">
                  <MiniTopo present={vlanPresent(run, at)} label={at != null || !run.fixture?.live ? "from the run's recorded events" : "read from sw1 (≤ 5 s old)"} />
                  {validating && <div className="th-panel"><Countdown s={validating} now={at ?? now} /></div>}
                  {final && (
                    <div className={`th-final ${final.label === "Verified" ? "ok" : "bad"}`} role="status">
                      <span className="th-final-i" aria-hidden>{final.label === "Verified" ? "✓" : "!"}</span>
                      <div><b>{final.label}</b><div className="th-dim">{hms(final.t)} · {final.detail}</div></div>
                    </div>
                  )}
                </div>
                <div className="th-right">
                  <StepList stages={run.stages} at={at} now={now} />
                </div>
              </div>
              <p className="th-foot">Every time shown is a recorded event: the demo-break audit row, the action's timeline and commit records, and its validation window. Durations are differences between two of them.</p>
            </>
          )}
        </Load>
      </div>
    </>
  );
}
