import { useEffect, useState } from "react";
import { api } from "../api";
import { DISCLAIMER, Mark } from "../App";
import { DeletionLine, EraseData } from "../components/Erase";
import { Busy } from "../components/ui";

type Status = { valid: boolean; reason: string | null; retention_days: number; lease_minutes: number; contact: string | null; opens_at?: number; closes_at?: number };
const WHY: Record<string, string> = {
  used: "This invite has already been used. Each link works once.",
  expired: "This invite has expired. Ask for a new one.",
  revoked: "This invite was withdrawn.",
  invalid: "This invite link is not valid.",
  early: "Your session has not opened yet.",
};
const at = (t: number) => new Date(t * 1000).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });

/** The invite landing page. Opening it never uses the invite up; only "Start demo" does. */
export default function Start({ onDone }: { onDone: () => void }) {
  const token = new URLSearchParams(location.search).get("i") ?? "";
  const [st, setSt] = useState<Status | null>(null);
  const [consent, setConsent] = useState(false);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api("/api/whoami").catch(() => {}); // sets the CSRF cookie
    api<Status>(`/api/invites/status?i=${encodeURIComponent(token)}`).then(setSt).catch((e) => setErr(e.message));
  }, [token]);
  const start = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/api/invites/redeem", { json: { token, consent, display_name: name.trim() || null } });
      onDone();
    } catch (e: any) {
      setErr(e.status === 429 ? "Too many attempts. Wait a minute." : e.status === 425 ? "Your session has not opened yet. Try again at the time shown." : e.message);
    } finally {
      setBusy(false);
    }
  };
  const days = st?.retention_days ?? 90;
  return (
    <main className="login-wrap">
      <section className="login-art">
        <div className="brand" style={{ padding: 0 }}><Mark size={34} /><div style={{ fontSize: 20 }}>Shimpaku<small>home-lab assurance</small></div></div>
        <div>
          <p style={{ fontSize: 22, lineHeight: "30px", color: "#fff", fontWeight: 600, maxWidth: 420, margin: 0 }}>You're invited to drive a live network lab.</p>
          <p style={{ color: "#8FA79C", maxWidth: 420 }}>Break one VLAN on a real switch, watch an agent propose the fix, approve it, and watch it validate.</p>
        </div>
        <div className="small" style={{ color: "#8FA79C" }}>{DISCLAIMER}</div>
      </section>
      <section className="login-form">
        <form onSubmit={start} style={{ width: 420, maxWidth: "100%" }} aria-labelledby="start-h">
          <h1 id="start-h" style={{ marginBottom: 4 }}>Live session</h1>
          {st?.opens_at && st.closes_at && (
            <p className="small" style={{ marginTop: 0 }}>
              Your slot: opens {at(st.opens_at)}, closes {at(st.closes_at)}. <a href={`/api/invites/ics?i=${encodeURIComponent(token)}`} download>Add to calendar</a>
            </p>
          )}
          {st && !st.valid ? (
            <div className={`notice ${st.reason === "early" ? "info" : "crit"}`} role="alert">
              {WHY[st.reason ?? "invalid"] ?? WHY.invalid}
              {st.reason === "early" && st.opens_at && <> It opens at <b>{at(st.opens_at)}</b>; keep this link and come back then.</>}
              {st.reason !== "early" && <> <a href="/request">Request a new session</a>.</>}
              {st.reason !== "invalid" && (
                <EraseData path="/api/invites/erase" body={{ token }} what="Your display name, the invite note and any request behind this link" />
              )}
            </div>
          ) : (
            <>
              <p className="weak" style={{ marginTop: 0 }}>A {st?.lease_minutes ?? 30}-minute session on the demo fixture. If someone else is using the lab, you join a queue.</p>
              <div className="notice info" style={{ marginBottom: 14 }}>
                <b>Before you start</b>
                <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                  <li><b>Purpose:</b> to know who tried this demo and follow up with them.</li>
                  <li><b>What is kept:</b> your request (name, contact, message) or the owner's note about you, the display name below if you give one, the times of your session, and what you do in it (your actions are audited under a pass id, not your name).</li>
                  <li><b>How long:</b> your name, contact, message and the owner's note are deleted {days} days after your session ends. The audit trail keeps only the pass id.</li>
                  <DeletionLine days={days} contact={st?.contact} />
                </ul>
              </div>
              <div className="field">
                <label htmlFor="dn">Display name (optional)</label>
                <input id="dn" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} autoComplete="off" />
              </div>
              <label className="small" style={{ display: "flex", gap: 8, alignItems: "flex-start", marginBottom: 14 }}>
                <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} required />
                <span>I have read this notice and agree to this data being kept as described.</span>
              </label>
              {err && <div className="notice crit" role="alert" style={{ marginBottom: 12 }}>{err}</div>}
              <button className="btn primary" type="submit" disabled={busy || !consent || !st} style={{ width: "100%", justifyContent: "center", padding: "9px" }}>
                {busy && <Busy />} Start demo
              </button>
            </>
          )}
        </form>
      </section>
    </main>
  );
}
