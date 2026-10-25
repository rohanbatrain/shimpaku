// The README architecture as a clickable diagram. Every path and evidence item names a real file in
// this repository; nothing here is fetched from the lab.
import type { KeyboardEvent } from "react";
import { useSearchParams } from "react-router";
import TopBar from "../components/TopBar";
import "../public.css";

const REPO: string = ((import.meta as any).env?.VITE_REPO_URL ?? "").replace(/\/$/, "");

type Part = {
  id: string; title: string; sub: string; x: number; y: number; w: number; h: number;
  what: string[]; files: string[]; evidence: { label: string; path: string }[];
};

const PARTS: Part[] = [
  { id: "fabric", title: "Fabric", sub: "spine1/2 · leaf1/2", x: 30, y: 82, w: 130, h: 52,
    what: ["vJunos switches: OSPF + BFD underlay, EVPN-VXLAN.", "Reachable only from the lab host's management network; never from the internet."],
    files: ["src/shimpaku/devices.py", "src/shimpaku/topology.py"], evidence: [{ label: "Topology parsing tests", path: "tests/collector/test_topology.py" }] },
  { id: "campus", title: "Campus", sub: "r1 · r2 · sw1", x: 30, y: 168, w: 130, h: 52,
    what: ["The OSPF exercise (r1–r2) and the access switch sw1 with the demo fixture port.", "RPM probes run from these devices; their results feed app reachability and the probe heatmap."],
    files: ["src/shimpaku/devices.py", "src/shimpaku/actions/fixture.py", "docs/build/rpm-config.set"], evidence: [{ label: "RPM parsing tests", path: "tests/analytics/test_analytics.py" }] },
  { id: "collector", title: "Collector", sub: "gNMI dial-in · events · RPM", x: 220, y: 70, w: 132, h: 52,
    what: ["gNMI dial-in telemetry, Junos events and RPM probe results into the store.", "Read-only: it never writes a device."],
    files: ["src/shimpaku/collector/poller.py", "src/shimpaku/collector/parse.py", "src/shimpaku/analytics/rpm.py"],
    evidence: [{ label: "Parser and poller tests", path: "tests/collector/test_poller.py" }, { label: "Telemetry notes", path: "docs/build/telemetry.md" }] },
  { id: "store", title: "Store", sub: "DuckDB + SQLite", x: 220, y: 154, w: 132, h: 52,
    what: ["DuckDB: metrics, events, probe results and probe-minutes, incidents.", "SQLite: actions, sessions, tokens and the hash-chained audit log."],
    files: ["src/shimpaku/store/schema.sql", "src/shimpaku/store/__init__.py", "src/shimpaku/mcp/audit.py"],
    evidence: [{ label: "Store tests", path: "tests/store/test_store.py" }, { label: "Store API", path: "docs/build/store-api.md" }] },
  { id: "analytics", title: "Analytics", sub: "SLE · blast radius · detectors", x: 220, y: 238, w: 132, h: 52,
    what: ["Service levels in device- and probe-minutes, with n beside every percentage.", "Blast radius with a significance gate; incidents; the probe heatmap grid."],
    files: ["src/shimpaku/analytics/sle.py", "src/shimpaku/analytics/blast.py", "src/shimpaku/analytics/incidents.py", "src/shimpaku/analytics/heatmap.py"],
    evidence: [{ label: "Analytics tests", path: "tests/analytics/test_analytics.py" }, { label: "Analytics notes", path: "docs/build/analytics.md" }] },
  { id: "mcp", title: "MCP server", sub: "the only path to a device", x: 392, y: 70, w: 160, h: 92,
    what: ["One tool surface for the dashboard, Shim and external agents.", "T0 reads; T1 reversible and T2 disruptive actions can only be proposed: a proposal returns an action id, never a change.", "Every call is written to the hash-chained audit."],
    files: ["src/shimpaku/mcp/server.py", "src/shimpaku/mcp/serve.py", "src/shimpaku/service.py", "src/shimpaku/mcp/audit.py"],
    evidence: [{ label: "MCP server tests", path: "tests/mcp/test_server.py" }, { label: "MCP and actions notes", path: "docs/build/mcp-actions.md" }] },
  { id: "guardrails", title: "Guardrails G0–G9", sub: "Python over live facts", x: 392, y: 190, w: 160, h: 66,
    what: ["Run on every proposal, before approval and before any device is touched.", "G0 argument shape · G1 port role · G2 bounce cap · G3 second-device VLAN check · G4 approval + commit confirmed · G5 one writer per device · G6 evidence · G7 no fix over a broken path · G8 drift · G9 driver-assist only.", "Approval is a dashboard session, not an MCP tool."],
    files: ["src/shimpaku/actions/policy.py", "src/shimpaku/mcp/guardrails_doc.py", "src/shimpaku/actions/engine.py"],
    evidence: [{ label: "Policy tests (one per guardrail)", path: "tests/actions/test_policy.py" }, { label: "Public red team", path: "tests/redteam/test_public_redteam.py" }] },
  { id: "executor", title: "Executor", sub: "lock · commit confirmed", x: 392, y: 284, w: 160, h: 56,
    what: ["Per-device lease (cross-process SQLite), exclusive candidate, commit confirmed.", "Confirms only after a fresh-session health check; if it dies, Junos rolls back (measured 73–76 s).", "Validation timers mark an action verified or recurred."],
    files: ["src/shimpaku/actions/executor.py", "src/shimpaku/lock.py", "src/shimpaku/actions/state.py"],
    evidence: [{ label: "Lock tests", path: "tests/test_lock.py" }, { label: "Engine tests", path: "tests/actions/test_engine.py" }, { label: "Incidents found by running it", path: "docs/INCIDENTS.md" }] },
  { id: "api", title: "FastAPI", sub: "REST + SSE", x: 592, y: 70, w: 120, h: 52,
    what: ["The dashboard's API, in the same process; calls the same service functions as MCP.", "Sessions, CSRF, tester leases, operator 2FA, rate limits."],
    files: ["src/shimpaku/api/app.py", "src/shimpaku/api/view.py", "src/shimpaku/api/routes_viz.py", "src/shimpaku/api/routes_public.py"],
    evidence: [{ label: "API tests", path: "tests/api/test_api.py" }, { label: "Security model", path: "SECURITY.md" }] },
  { id: "dashboard", title: "Dashboard", sub: "this app", x: 592, y: 154, w: 120, h: 52,
    what: ["Service levels, root cause, topology, actions queue with validation, benchmark.", "Approve and reject live here, never in an agent."],
    files: ["web/src/App.tsx", "web/src/pages/", "web/src/api.ts"], evidence: [{ label: "Web notes", path: "docs/build/web.md" }] },
  { id: "shim", title: "Shim", sub: "built-in assistant", x: 592, y: 238, w: 120, h: 52,
    what: ["An MCP client inside the daemon: reads with T0 tools and proposes, like any agent.", "Its model goes through the operator's LLM gateway; off on the public replay."],
    files: ["src/shimpaku/assistant/loop.py", "src/shimpaku/assistant/routing.py", "src/shimpaku/assistant/prompts.py"],
    evidence: [{ label: "Assistant eval gate", path: "tests/assistant/test_eval_gate.py" }, { label: "Assistant notes", path: "docs/build/assistant.md" }] },
  { id: "agents", title: "External agents", sub: "Claude Code, OpenCode", x: 752, y: 82, w: 128, h: 52,
    what: ["Bring their own model; connect over stdio-over-ssh or HTTP with a bearer token.", "Same tools, same guardrails, same audit as the dashboard."],
    files: ["src/shimpaku/mcp/serve.py", "src/shimpaku/mcp/proxy.py", "src/shimpaku/api/auth.py"], evidence: [{ label: "Integration notes", path: "docs/build/integration.md" }] },
  { id: "bench", title: "Virga bench", sub: "own ground truth", x: 752, y: 252, w: 128, h: 52,
    what: ["A separate process injecting faults over its own NETCONF, under the same device lock.", "Reads only the audit; the system under test cannot read its catalogue."],
    files: ["bench/virga/runner.py", "bench/virga/scoring.py", "bench/catalogue.yaml"], evidence: [{ label: "Bench notes", path: "docs/build/bench.md" }] },
  { id: "edge", title: "VPS gateway", sub: "Cloudflare → Caddy", x: 220, y: 438, w: 150, h: 56,
    what: ["TLS, per-address rate limits, security headers; reads go to the live lab over NetBird first, the replay second.", "Writes and MCP go to the live lab only; never replayed."],
    files: ["deploy/vps/Caddyfile", "deploy/vps/compose.yaml", "deploy/vps/Dockerfile"], evidence: [{ label: "VPS runbook", path: "deploy/vps/README.md" }, { label: "Deploy design", path: "docs/DEPLOY-DESIGN.md" }] },
  { id: "replay", title: "Replay", sub: "read-only snapshot", x: 410, y: 438, w: 142, h: 56,
    what: ["Serves a redacted snapshot recorded from the live lab when the lab is offline.", "No device credentials, no writes, no assistant; management addresses are dropped at export."],
    files: ["src/shimpaku/api/replay.py", "src/shimpaku/snapshot_cli.py"], evidence: [{ label: "Replay tests", path: "tests/api/test_replay.py" }] },
  { id: "landing", title: "Landing + status", sub: "static · Uptime Kuma", x: 592, y: 438, w: 150, h: 56,
    what: ["The static landing page with a live status strip (GET /api/public/summary, CORS allow-list).", "Uptime Kuma's public status page; its admin UI stays on the VPS loopback."],
    files: ["deploy/vps/site/index.html", "deploy/vps/site/live.js"], evidence: [{ label: "Public route tests", path: "tests/api/test_public.py" }] },
];

const EDGES: { d: string; write?: boolean; dash?: boolean }[] = [
  { d: "M160 108 C190 108 190 96 220 96" }, { d: "M160 194 C195 194 190 110 220 104" },
  { d: "M286 122 V154" }, { d: "M286 206 V238" },
  { d: "M352 264 C372 264 372 140 392 140" },
  { d: "M472 162 V190", write: true }, { d: "M472 256 V284", write: true },
  { d: "M392 320 C190 360 120 300 110 220", write: true },
  { d: "M592 96 H552" }, { d: "M652 154 V122" }, { d: "M592 264 C572 264 572 150 552 150" },
  { d: "M752 108 C700 40 560 40 520 70" }, { d: "M816 304 V376 H95 V222", dash: true },
  { d: "M370 466 H410" }, { d: "M295 438 V396 H722 V96 H714" }, { d: "M592 466 H552", dash: true },
];

export default function Architecture() {
  const [sp, setSp] = useSearchParams();
  const sel = PARTS.find((p) => p.id === sp.get("c")) ?? PARTS.find((p) => p.id === "mcp")!;
  const pick = (id: string) => { sp.set("c", id); setSp(sp, { replace: true }); };
  const key = (e: KeyboardEvent, id: string) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(id); } };
  const link = (path: string) => (REPO ? <a href={`${REPO}/blob/main/${path}`} target="_blank" rel="noreferrer"><code>{path}</code></a> : <code>{path}</code>);
  return (
    <>
      <TopBar title="Architecture" crumbs="How it is built" />
      <div className="content">
        <p className="weak" style={{ margin: 0 }}>One process (<code className="mono">shimpakud</code>) owns state; one MCP server is the only path to a device. Click a component.</p>
        <div className="arch-list" role="group" aria-label="Components">
          {PARTS.map((p) => <button key={p.id} type="button" aria-pressed={p.id === sel.id} onClick={() => pick(p.id)}>{p.title}</button>)}
        </div>
        <div className="arch">
          <div className="card arch-card">
            <div className="arch-scroll">
              <svg className="arch-svg" viewBox="0 0 900 510" role="group" aria-label="Architecture diagram">
                <defs>
                  <marker id="ah" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L8 4 L0 8 z" fill="#8a9a93" /></marker>
                  <marker id="aw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L8 4 L0 8 z" fill="#00785c" /></marker>
                </defs>
                <rect className="zone-r" x="16" y="50" width="158" height="190" rx="8" /><text className="zone" x="26" y="68">Lab · libvirt</text>
                <rect className="zone-r" x="204" y="34" width="524" height="330" rx="8" /><text className="zone" x="214" y="52">shimpakud · one process, one port</text>
                <rect className="zone-r" x="740" y="50" width="152" height="270" rx="8" /><text className="zone" x="750" y="68">Outside</text>
                <rect className="zone-r" x="204" y="404" width="550" height="104" rx="8" /><text className="zone" x="214" y="424">VPS · public edge</text>
                {EDGES.map((e, i) => <path key={i} d={e.d} className={`edge${e.write ? " write" : ""}`} strokeDasharray={e.dash ? "5 4" : undefined} markerEnd={`url(#${e.write ? "aw" : "ah"})`} />)}
                <text className="sub" x="200" y="352" style={{ fill: "var(--p-ink)" }}>write path: PyEZ, commit confirmed</text>
                <text className="sub" x="380" y="392">/api reads: live lab over NetBird first, replay second</text>
                <text className="sub" x="560" y="372">Virga: own NETCONF, same device lock</text>
                {PARTS.map((p) => (
                  <g key={p.id} className={`box${p.id === sel.id ? " sel" : ""}`} tabIndex={0} role="button" aria-pressed={p.id === sel.id}
                    aria-label={`${p.title}: ${p.sub}`} onClick={() => pick(p.id)} onKeyDown={(e) => key(e, p.id)}>
                    <rect x={p.x} y={p.y} width={p.w} height={p.h} rx="6" />
                    <text x={p.x + 10} y={p.y + 21} style={{ fontWeight: 650 }}>{p.title}</text>
                    <text className="sub" x={p.x + 10} y={p.y + 37}>{p.sub}</text>
                    {p.id === "mcp" && ["T0", "T1", "T2"].map((t, i) => (
                      <g key={t}><rect className="g" x={p.x + 10 + i * 44} y={p.y + 52} width="38" height="18" rx="4" /><text className="gt" x={p.x + 20 + i * 44} y={p.y + 65}>{t}</text></g>
                    ))}
                    {p.id === "guardrails" && Array.from({ length: 10 }).map((_, i) => (
                      <g key={i}><rect className="g" x={p.x + 8 + i * 14.6} y={p.y + 44} width="13" height="14" rx="2" /><text className="gt" x={p.x + 10.5 + i * 14.6} y={p.y + 54.5} style={{ fontSize: 7.5 }}>{i}</text></g>
                    ))}
                  </g>
                ))}
              </svg>
            </div>
          </div>
          <aside className="card arch-panel" aria-live="polite">
            <h3>{sel.title}</h3>
            <div className="small weak">{sel.sub}</div>
            <h2 style={{ marginTop: 12 }}>What it does</h2>
            <ul>{sel.what.map((w) => <li key={w}>{w}</li>)}</ul>
            <h2 style={{ marginTop: 12 }}>Code</h2>
            <ul>{sel.files.map((f) => <li key={f}>{link(f)}</li>)}</ul>
            <h2 style={{ marginTop: 12 }}>Evidence</h2>
            <ul>{sel.evidence.map((e) => <li key={e.path}>{e.label}: {link(e.path)}</li>)}</ul>
          </aside>
        </div>
      </div>
    </>
  );
}
