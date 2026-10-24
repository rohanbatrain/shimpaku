import { useEffect, useState } from "react";
import { api } from "../api";
import { DISCLAIMER, Mark } from "../App";
import { Busy } from "../components/ui";

export default function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api("/api/whoami").catch(() => {}); }, []); // sets the CSRF cookie
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/api/login", { json: { username: "admin", password: pw } });
      onDone();
    } catch (e: any) {
      setErr(e.status === 429 ? "Too many attempts. Wait a minute and try again." : e.status === 401 ? "Wrong password." : e.message);
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
            <input id="p" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" autoFocus required />
          </div>
          {err && <div className="notice crit" role="alert" style={{ marginBottom: 12 }}>{err}</div>}
          <button className="btn primary" type="submit" disabled={busy || !pw} style={{ width: "100%", justifyContent: "center", padding: "9px" }}>
            {busy && <Busy />} Sign in
          </button>
          <p className="small weak">Set the password on the host with <span className="mono">shimpaku admin set-password</span>.</p>
          <p className="small">Not the operator? <a href="/request">Request a live session</a>.</p>
        </form>
      </section>
    </main>
  );
}
