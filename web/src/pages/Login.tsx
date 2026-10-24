import { useEffect, useState } from "react";
import { api } from "../api";
import { DISCLAIMER, Mark } from "../App";
import { Busy } from "../components/ui";
import type { Who } from "../session";

export default function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [who, setWho] = useState<Who | null>(null);
  useEffect(() => { api<Who>("/api/whoami").then(setWho).catch(() => {}); }, []); // also sets the CSRF cookie
  const pub = !!who?.public_view;
  const needCode = who ? who.totp_required !== false : true;
  const idle = who?.operator_idle_min ?? 30;
  const codeOk = !needCode || (recovery ? code.trim().length >= 6 : /^\d{6}$/.test(code));
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, string> = { username: "admin", password: pw };
      if (needCode) body[recovery ? "recovery_code" : "code"] = code.trim();
      await api("/api/login", { json: body });
      setPw("");
      setCode("");
      onDone();
    } catch (e: any) {
      setCode("");
      setErr(e.status === 429 ? "Too many attempts. Wait and try again."
        : e.status === 401 ? (needCode ? "Wrong password or code." : "Wrong password.")
        : e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="login-wrap">
      <section className="login-art">
        <div className="brand" style={{ padding: 0 }}><Mark size={34} /><div style={{ fontSize: 20 }}>Shimpaku<small>home-lab assurance</small></div></div>
        <div>
          <p style={{ fontSize: 22, lineHeight: "30px", color: "#fff", fontWeight: 600, maxWidth: 420, margin: 0 }}>Service levels, root cause and guarded changes for one Junos lab.</p>
          <p style={{ color: "#8FA79C", maxWidth: 420 }}>Every change is proposed with evidence, approved by a person, and committed with an automatic rollback.</p>
        </div>
        <div className="small" style={{ color: "#8FA79C" }}>{DISCLAIMER}</div>
      </section>
      <section className="login-form">
        <form onSubmit={submit} style={{ width: 340, maxWidth: "100%" }} aria-labelledby="login-h">
          <h1 id="login-h" style={{ marginBottom: 4 }}>Sign in</h1>
          <p className="weak" style={{ marginTop: 0 }}>Local admin account (auth mode <span className="mono">local</span>).</p>
          <div className="field">
            <label htmlFor="u">User</label>
            <input id="u" value="admin" readOnly autoComplete="username" />
          </div>
          <div className="field">
            <label htmlFor="p">Password</label>
            {/* autocomplete=off on the public site: a hint only, browsers may still offer to save */}
            <input id="p" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete={pub ? "off" : "current-password"} autoFocus required />
          </div>
          {needCode && (
            <div className="field">
              <label htmlFor="c">{recovery ? "Recovery code" : "Authenticator code"}</label>
              {recovery ? (
                <input id="c" key="r" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="xxxxx-xxxxx" required />
              ) : (
                <input id="c" key="t" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" placeholder="6 digits" required />
              )}
              <button type="button" className="linkish small" onClick={() => { setRecovery(!recovery); setCode(""); }}>
                {recovery ? "Use the authenticator code" : "Use a recovery code"}
              </button>
            </div>
          )}
          {pub && <div className="notice small" style={{ marginBottom: 12 }}>On a shared computer: sign out when done; this session ends after {idle} minutes idle.</div>}
          {err && <div className="notice crit" role="alert" style={{ marginBottom: 12 }}>{err}</div>}
          <button className="btn primary" type="submit" disabled={busy || !pw || !codeOk} style={{ width: "100%", justifyContent: "center", padding: "9px" }}>
            {busy && <Busy />} Sign in
          </button>
          <p className="small weak">Set the password on the host with <span className="mono">shimpaku admin set-password</span>.</p>
          <p className="small">Not the operator? <a href="/request">Request a live session</a>.</p>
        </form>
      </section>
    </main>
  );
}
