import { useState } from "react";
import { Layer } from "grommet";
import { Copy } from "grommet-icons";
import { api, useApi } from "../api";
import TopBar from "../components/TopBar";
import { Busy, Empty, Load, Pill } from "../components/ui";
import { ago } from "../format";

type Tok = { id: string; name: string; scope: string; created: number; created_by: string; last_used: number | null; revoked: number | null };

export default function Agents() {
  const q = useApi<{ tokens: Tok[] }>("/api/tokens");
  const [name, setName] = useState("");
  const [scope, setScope] = useState("read");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [made, setMade] = useState<{ name: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const mcp = `${location.origin}/mcp`;

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ name: string; token: string }>("/api/tokens", { json: { name, scope } });
      setMade(r);
      setName("");
      q.reload();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  const revoke = async (id: string) => {
    await api(`/api/tokens/${id}`, { method: "DELETE" }).catch((e) => setErr(e.message));
    q.reload();
  };

  return (
    <>
      <TopBar title="Connected agents" crumbs="Bearer tokens for MCP clients (Claude Code, OpenCode, scripts)" />
      <div className="content">
        <div className="grid2">
          <div className="card">
            <h2>New token</h2>
            <form onSubmit={create}>
              <div className="field">
                <label htmlFor="tn">Name</label>
                <input id="tn" value={name} onChange={(e) => setName(e.target.value)} placeholder="claude-code-laptop" pattern="[A-Za-z0-9 ._\-]+" required maxLength={64} />
              </div>
              <div className="field">
                <label htmlFor="ts">Scope</label>
                <select id="ts" value={scope} onChange={(e) => setScope(e.target.value)}>
                  <option value="read">read — every read tool</option>
                  <option value="propose">propose — read, plus propose and cancel actions</option>
                </select>
              </div>
              {err && <div className="notice crit" role="alert" style={{ marginBottom: 12 }}>{err}</div>}
              <button className="btn primary" disabled={busy || !name}>{busy && <Busy />} Create token</button>
            </form>
          </div>
          <div className="card">
            <h2>How agents connect</h2>
            <p style={{ marginTop: 0 }}>MCP over Streamable HTTP at <span className="mono">{mcp}</span> with <span className="mono">Authorization: Bearer &lt;token&gt;</span>, or stdio through <span className="mono">ssh sitar shimpaku mcp-stdio</span>.</p>
            <div className="notice info"><b>No token can approve.</b> Approval is a click by a signed-in person on the action page. Agents propose with evidence; the executor commits with <span className="mono">commit confirmed</span>.</div>
          </div>
        </div>
        <div className="card flush">
          <div className="card-h"><h2>Tokens</h2><span className="small weak">only a SHA-256 digest is stored; the value is shown once</span></div>
          <Load q={q} empty={(d) => (d.tokens.length ? null : <Empty title="No tokens yet">Create one above to connect an agent.</Empty>)}>
            {(d) => (
              <table className="t">
                <thead><tr><th>Name</th><th>Scope</th><th>ID</th><th>Created</th><th>Last used</th><th>State</th><th /></tr></thead>
                <tbody>
                  {d.tokens.map((t) => (
                    <tr key={t.id}>
                      <td><b>{t.name}</b></td>
                      <td><Pill tone={t.scope === "propose" ? "warn" : "neutral"}>{t.scope}</Pill></td>
                      <td className="mono small">{t.id}</td>
                      <td className="small weak">{ago(t.created)} by {t.created_by}</td>
                      <td className="small weak">{t.last_used ? ago(t.last_used) : "never"}</td>
                      <td>{t.revoked ? <Pill tone="neutral">Revoked</Pill> : <Pill tone="ok">Active</Pill>}</td>
                      <td style={{ textAlign: "right" }}>{!t.revoked && <button className="btn danger" onClick={() => revoke(t.id)}>Revoke</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Load>
        </div>
      </div>
      {made && (
        <Layer onEsc={() => setMade(null)} modal responsive={false}>
          <div style={{ padding: 22, width: 560 }} role="dialog" aria-labelledby="tok-h">
            <h1 id="tok-h" style={{ fontSize: 17 }}>Token for {made.name}</h1>
            <p className="weak">Copy it now. It is not shown again.</p>
            <div className="diff" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{made.token}</div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 14 }}>
              <button className="btn" onClick={() => navigator.clipboard?.writeText(made.token).then(() => setCopied(true))}><Copy size="14px" /> {copied ? "Copied" : "Copy"}</button>
              <button className="btn primary" onClick={() => { setMade(null); setCopied(false); }}>Done</button>
            </div>
          </div>
        </Layer>
      )}
    </>
  );
}
