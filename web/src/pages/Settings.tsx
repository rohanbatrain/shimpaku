import { Fragment, useState } from "react";
import { signOutAndClear, useSession } from "../session";
import { useApi } from "../api";
import TopBar from "../components/TopBar";
import { Load, Pill } from "../components/ui";

type S = { auth_mode: string; demo: boolean; https: boolean; cookie_secure: boolean; session_cookie: string; csrf_cookie: string; cookie_warning: string | null; bind: string; port: number; mcp_url: string; mcp_loopback_only: boolean; models: Record<string, string>; token_scopes: string[]; admin_configured: boolean; public_view?: boolean; totp_enabled?: boolean; operator_session?: { idle_min: number; max_h: number } };

function fmtDur(min: number): string {
  return min >= 60 ? `${+(min / 60).toFixed(1)} h` : `${min} min`;
}

export default function Settings() {
  const q = useApi<S>("/api/settings");
  const { who } = useSession();
  const [busy, setBusy] = useState(false);
  const everywhere = () => {
    if (!confirm("Sign out every operator session, on every browser, including this one?")) return;
    setBusy(true);
    void signOutAndClear(true);
  };
  return (
    <>
      <TopBar title="Settings" crumbs="Read-only: set by environment on the host" />
      <div className="content">
        <Load q={q} rows={6}>
          {(s) => (
            <>
              {s.cookie_warning && <div className="notice warn" role="alert"><b>Session cookie is not Secure.</b> {s.cookie_warning}</div>}
              {s.auth_mode === "off" && (
                <div className="notice warn"><b>Auth is off.</b> Anyone who reaches this port acts as <span className="mono">operator</span>. MCP over HTTP is refused on a non-loopback bind while auth is off. Set <span className="mono">SHIMPAKU_AUTH_MODE=local</span> to require sign-in.</div>
              )}
              <div className="grid2">
                <div className="card">
                  <h2>Authentication</h2>
                  <dl className="kv">
                    <dt>Mode</dt><dd><Pill tone={s.auth_mode === "local" ? "ok" : "warn"}>{s.auth_mode}</Pill> <span className="mono small weak">SHIMPAKU_AUTH_MODE</span></dd>
                    <dt>Admin password</dt><dd>{s.admin_configured ? "set (argon2id)" : <span className="tone-warn">not set — run shimpaku admin set-password</span>}</dd>
                    <dt>Transport</dt><dd>{s.https ? "https" : "http"}</dd>
                    <dt>Session cookie</dt><dd className="mono">{s.session_cookie}{s.cookie_secure ? "; Secure" : ""}; HttpOnly; SameSite=Lax; Path=/</dd>
                    <dt>CSRF</dt><dd><span className="mono">{s.csrf_cookie}</span> double-submit in <span className="mono">X-CSRF-Token</span>, plus an Origin check</dd>
                    <dt>Two-factor</dt><dd>{s.totp_enabled ? "TOTP on (authenticator code or one-time recovery code)" : s.public_view ? <span className="tone-warn">required on the public site — run shimpaku admin totp enable</span> : "off — shimpaku admin totp enable"}</dd>
                    <dt>Sessions</dt><dd>{s.operator_session ? `${fmtDur(s.operator_session.idle_min)} idle, ${fmtDur(s.operator_session.max_h * 60)} absolute` : "12 h idle, 7 d absolute"} · browser-session cookie · sign-in limited to 5 per minute</dd>
                  </dl>
                  {who.mode === "local" && who.role === "operator" && (
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                      <button className="btn" onClick={() => void signOutAndClear()}>Sign out</button>
                      <button className="btn danger" onClick={everywhere} disabled={busy}>Sign out everywhere</button>
                    </div>
                  )}
                </div>
                <div className="card">
                  <h2>Service</h2>
                  <dl className="kv">
                    <dt>Bind</dt><dd className="mono">{s.bind}:{s.port}</dd>
                    <dt>MCP endpoint</dt><dd className="mono">{s.mcp_url}{s.mcp_loopback_only ? " (loopback only)" : ""}</dd>
                    <dt>Token scopes</dt><dd>{s.token_scopes.map((x) => <span key={x} className="chip">{x}</span>)} <span className="small weak">never approve</span></dd>
                    <dt>Data</dt><dd>{s.demo ? <Pill tone="warn">demo — canned data, no devices</Pill> : <Pill tone="ok">live</Pill>}</dd>
                    {Object.entries(s.models).map(([role, m]) => (
                      <Fragment key={role}><dt>Model · {role}</dt><dd className="mono">{m}</dd></Fragment>
                    ))}
                  </dl>
                </div>
              </div>
            </>
          )}
        </Load>
      </div>
    </>
  );
}
