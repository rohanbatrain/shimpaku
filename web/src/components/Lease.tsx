import { useEffect, useState } from "react";
import { api, useApi } from "../api";
import { useSession } from "../session";
import { Busy } from "./ui";
import { fixtureArgs } from "./fixture";

export type Lease = { id: string; state: string; remaining_s: number | null; queue_position: number | null; breaks_left: number; seeds_left: number; token_issued: boolean; ends: number | null };
type LeaseResp = { role: string; lease: Lease | null; assistant?: boolean };

export function useLease(on: boolean) {
  const q = useApi<LeaseResp>(on ? "/api/lease" : null);
  useEffect(() => {
    if (!on) return;
    const t = setInterval(q.reload, 10000);
    return () => clearInterval(t);
  }, [on, q.reload]);
  return q;
}

function mmss(s: number) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Remaining time, queue position and "End session" for a tester. */
export function LeaseBanner() {
  const { refresh } = useSession();
  const q = useLease(true);
  const l = q.data?.lease;
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const [busy, setBusy] = useState(false);
  const end = async () => {
    if (!confirm("End your session? Your open actions are cancelled and the lab is reset.")) return;
    setBusy(true);
    await api("/api/lease/end", { method: "POST" }).catch(() => {});
    refresh();
  };
  if (!l) return null;
  const left = l.state === "active" && l.ends ? Math.max(0, Math.round(l.ends - now / 1000)) : null;
  return (
    <div className={`notice lease ${l.state === "active" ? "ok" : "warn"}`} role="status">
      <span style={{ flex: 1 }}>
        {l.state === "active" ? <>Tester session · <b className="mono">{left != null ? mmss(left) : "—"}</b> left · {l.breaks_left} breaks, {l.seeds_left} re-seeds left</>
          : l.state === "queued" ? <>Someone else is using the lab. You are <b>#{l.queue_position}</b> in the queue; this page updates by itself.</>
          : <>Your session has ended.</>}
      </span>
      {l.state !== "ended" && <button className="btn" onClick={end} disabled={busy}>{busy && <Busy />} End session</button>}
    </div>
  );
}

/** The demo fixture flow on the Actions page: break it, propose the fix, re-seed, connect an agent. */
export function FixturePanel({ tester }: { tester: boolean }) {
  const lease = useLease(tester);
  const active = !tester || lease.data?.lease?.state === "active";
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [tok, setTok] = useState<{ token: string; mcp_url: string } | null>(null);
  const run = async (label: string, f: () => Promise<any>, ok: (r: any) => string) => {
    setBusy(label);
    setMsg(null);
    try {
      setMsg({ tone: "ok", text: ok(await f()) });
    } catch (e: any) {
      setMsg({ tone: "crit", text: e.message });
    } finally {
      setBusy(null);
      lease.reload();
    }
  };
  const cmd = tok ? `claude mcp add --transport http shimpaku ${tok.mcp_url} --header "Authorization: Bearer ${tok.token}"` : "";
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Demo fixture · VLAN 999 on sw1 ge-0/0/5</h2>
      <p className="weak small" style={{ marginTop: 0 }}>1. Break removes VLAN 999 from the trunk (a real commit). 2. Propose the fix, or let Shim or your own agent propose it. 3. Open the action and approve it. 4. Watch it validate for 3 minutes.</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn danger" disabled={!active || !!busy} onClick={() => run("break", () => api("/api/demo/break", { method: "POST" }), (r) => (r.changed ? "VLAN 999 removed from the trunk." : "Already broken: nothing changed."))}>{busy === "break" && <Busy />} Break VLAN 999</button>
        <button className="btn primary" disabled={!active || !!busy} onClick={() => run("propose", () => api("/api/actions", { json: { kind: "add_vlan_to_trunk", args: fixtureArgs, reason: "restore VLAN 999 on the demo trunk" } }), (r) => `Proposed ${r.id}: ${r.status.replace(/_/g, " ")}. Open it from the list below to approve.`)}>{busy === "propose" && <Busy />} Propose the fix</button>
        <button className="btn" disabled={!active || !!busy} onClick={() => run("seed", () => api("/api/demo/seed", { method: "POST" }), (r) => (r.changed ? "Fixture re-seeded." : "Fixture already in place."))}>{busy === "seed" && <Busy />} Re-seed</button>
        {tester && <button className="btn" disabled={!active || !!busy || !!lease.data?.lease?.token_issued} onClick={() => run("token", async () => { const t = await api("/api/lease/token", { method: "POST" }); setTok(t); return t; }, () => "Token created. Copy it now: it is shown only once.")}>{busy === "token" && <Busy />} Connect your own agent</button>}
      </div>
      {msg && <div className={`notice ${msg.tone}`} role="status" style={{ marginTop: 12 }}>{msg.text}</div>}
      {tok && (
        <div className="notice warn" style={{ marginTop: 12 }}>
          <b>Your MCP token (shown once; it stops working when your session ends).</b> Scope: propose, the demo fixture only. Run:
          <pre className="mono" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", margin: "8px 0 0", userSelect: "all" }}>{cmd}</pre>
        </div>
      )}
      {tester && !active && <p className="small weak" style={{ marginBottom: 0 }}>These unlock when your session starts.</p>}
    </div>
  );
}
