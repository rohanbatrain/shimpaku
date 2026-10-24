import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import { DISCLAIMER, Mark } from "../App";
import { DeletionLine } from "../components/Erase";
import { Busy } from "../components/ui";

export type Notice = { retention_days: number; contact: string | null; lease_minutes: number; max_slots: number; open: boolean };

export const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
export const when = (t: number) =>
  new Date(t * 1000).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });

/** datetime-local value (browser time) ⇄ epoch seconds. */
export const toLocalInput = (t: number) => {
  const d = new Date(t * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};
export const fromLocalInput = (v: string) => new Date(v).getTime() / 1000;

/** The two-column public layout shared with Sign in and the invite page. */
export function PublicFrame({ lead, sub, children }: { lead: string; sub: string; children: ReactNode }) {
  return (
    <main className="login-wrap">
      <section className="login-art">
        <a href="/" className="brand" style={{ padding: 0, color: "#fff" }}><Mark size={34} /><div style={{ fontSize: 20 }}>Shimpaku<small>home-lab assurance</small></div></a>
        <div>
          <p style={{ fontSize: 22, lineHeight: "30px", color: "#fff", fontWeight: 600, maxWidth: 420, margin: 0 }}>{lead}</p>
          <p style={{ color: "#8FA79C", maxWidth: 420 }}>{sub}</p>
        </div>
        <div className="small" style={{ color: "#8FA79C" }}>{DISCLAIMER}</div>
      </section>
      <section className="login-form">{children}</section>
    </main>
  );
}

const nextHour = () => Math.ceil(Date.now() / 3600000 + 1.05) * 3600;

/** Public: ask the owner for a live 30-minute session. Nothing here signs anyone in. */
export default function Request() {
  const [n, setN] = useState<Notice | null>(null);
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [message, setMessage] = useState("");
  const [slots, setSlots] = useState<string[]>([toLocalInput(nextHour() + 86400)]);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api("/api/whoami").catch(() => {}); // sets the CSRF cookie
    api<Notice>("/api/session-requests/notice").then(setN).catch((e) => setErr(e.message));
  }, []);
  const min = toLocalInput(Date.now() / 1000 + 3660), max = toLocalInput(Date.now() / 1000 + 30 * 86400 - 60);
  const days = n?.retention_days ?? 90;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const iso = slots.filter(Boolean).map((v) => new Date(fromLocalInput(v) * 1000).toISOString());
      const r = await api<{ status_token: string }>("/api/session-requests", { json: { name, contact, message, slots: iso, consent } });
      location.assign(`/request/status?s=${encodeURIComponent(r.status_token)}&new=1`);
    } catch (e: any) {
      setErr(e.status === 429 ? "Too many requests from your network. Try again later." : e.message);
      setBusy(false);
    }
  };

  return (
    <PublicFrame lead="Drive a live network lab for 30 minutes." sub="Break one VLAN on a real switch, watch an agent propose the fix, approve it, and watch it validate. Ask for a slot; the owner confirms one and sends you a link.">
      <form onSubmit={submit} style={{ width: 460, maxWidth: "100%" }} aria-labelledby="req-h">
        <h1 id="req-h" style={{ marginBottom: 4 }}>Request a live session</h1>
        <p className="weak" style={{ marginTop: 0 }}>
          A {n?.lease_minutes ?? 30}-minute session on the demo fixture. No account needed. Or <a href="/">look around read-only</a> first.
        </p>
        {n && !n.open ? <div className="notice warn" role="alert">This lab does not take requests.</div> : (
          <>
            <div className="field">
              <label htmlFor="rq-n">Your name</label>
              <input id="rq-n" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required autoComplete="name" />
            </div>
            <div className="field">
              <label htmlFor="rq-c">E-mail or LinkedIn URL</label>
              <input id="rq-c" value={contact} onChange={(e) => setContact(e.target.value)} maxLength={200} required autoComplete="email" inputMode="email" placeholder="you@example.com or linkedin.com/in/you" />
            </div>
            <div className="field">
              <label htmlFor="rq-m">Message (optional)</label>
              <textarea id="rq-m" value={message} onChange={(e) => setMessage(e.target.value)} maxLength={500} rows={3} />
              <span className="hint">{message.length}/500</span>
            </div>
            <fieldset className="field" style={{ border: 0, padding: 0, margin: "0 0 14px" }}>
              <legend style={{ fontSize: 13, fontWeight: 600, padding: 0, marginBottom: 4 }}>Preferred start times (up to {n?.max_slots ?? 3})</legend>
              <span className="hint" style={{ marginBottom: 4 }}>Your time zone: {TZ}. At least 1 hour and at most 30 days from now.</span>
              {slots.map((v, i) => (
                <div className="slot-row" key={i} style={{ marginBottom: 6 }}>
                  <input type="datetime-local" aria-label={`Preferred start ${i + 1}`} value={v} min={min} max={max} required={i === 0}
                    onChange={(e) => setSlots(slots.map((x, j) => (j === i ? e.target.value : x)))} style={{ font: "inherit", padding: "8px 10px", border: "1px solid var(--line2)", borderRadius: 6 }} />
                  {i > 0 && <button type="button" className="btn" onClick={() => setSlots(slots.filter((_, j) => j !== i))} aria-label={`Remove start ${i + 1}`}>Remove</button>}
                </div>
              ))}
              {slots.length < (n?.max_slots ?? 3) && (
                <button type="button" className="btn" style={{ alignSelf: "flex-start" }} onClick={() => setSlots([...slots, toLocalInput(fromLocalInput(slots[slots.length - 1] || toLocalInput(nextHour())) + 86400)])}>Add another time</button>
              )}
            </fieldset>
            <div className="notice info" style={{ marginBottom: 14 }}>
              <b>Before you send</b>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                <li><b>Purpose:</b> to schedule your session and reply to you.</li>
                <li><b>What is kept:</b> your name, contact, message and times; if you are approved, the times of your session and what you do in it (audited under a pass id, not your name).</li>
                <li><b>How long:</b> name, contact and message are deleted {days} days after your slot (or after a decline). The audit trail keeps only ids.</li>
                <DeletionLine days={days} contact={n?.contact} />
              </ul>
            </div>
            <label className="small" style={{ display: "flex", gap: 8, alignItems: "flex-start", marginBottom: 14 }}>
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} required style={{ width: 18, height: 18, flex: "none" }} />
              <span>I have read this notice and agree to this data being kept as described.</span>
            </label>
            {err && <div className="notice crit" role="alert" style={{ marginBottom: 12 }}>{err}</div>}
            <button className="btn primary" type="submit" disabled={busy || !consent || !n} style={{ width: "100%", justifyContent: "center", padding: "9px" }}>
              {busy && <Busy />} Send request
            </button>
          </>
        )}
      </form>
    </PublicFrame>
  );
}
