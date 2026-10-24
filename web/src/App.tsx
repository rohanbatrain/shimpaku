import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router";
import { Robot, Dashboard, Tree, Action, BarChart as BenchIcon, Connect, SettingsOption } from "grommet-icons";
import { api, useApi } from "./api";
import { SessionCtx, type Who } from "./session";
import { Loading } from "./components/ui";
import AssistantPanel from "./components/AssistantPanel";
import ServiceLevels from "./pages/ServiceLevels";
import RootCause, { RootCauseIndex } from "./pages/RootCause";
import Actions from "./pages/Actions";
import ActionDetail from "./pages/ActionDetail";
import Benchmark from "./pages/Benchmark";
import Login from "./pages/Login";
import Agents from "./pages/Agents";
import Settings from "./pages/Settings";

export function Mark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="7" fill="#16271F" />
      <path d="M9 22c2-7 6-11 14-12-3 3-4 6-4 10" fill="none" stroke="#01A982" strokeWidth="3" strokeLinecap="round" />
      <circle cx="10" cy="23" r="2.4" fill="#01A982" />
    </svg>
  );
}

export const DISCLAIMER = "Independent project. Not affiliated with HPE or Juniper Networks.";

function Nav({ who, onSignOut }: { who: Who; onSignOut: () => void }) {
  const acts = useApi<{ actions: { status: string }[] }>("/api/actions", { url: "/api/stream/actions", event: "actions" });
  const open = acts.data?.actions.filter((a) => ["proposed", "awaiting_approval"].includes(a.status)).length ?? 0;
  const ic = { size: "16px", color: "currentColor" } as const;
  return (
    <nav className="nav" aria-label="Main">
      <div className="brand">
        <Mark />
        <div>
          Shimpaku
          <small>home-lab assurance</small>
        </div>
      </div>
      <div className="sect">Monitor</div>
      <NavLink to="/sle">
        <Dashboard {...ic} /> Service levels
      </NavLink>
      <NavLink to="/root-cause">
        <Tree {...ic} /> Root cause
      </NavLink>
      <NavLink to="/actions">
        <Action {...ic} /> Actions
        {open > 0 && (
          <span className="count" aria-label={`${open} awaiting approval`}>
            {open}
          </span>
        )}
      </NavLink>
      <div className="sect">Evaluate</div>
      <NavLink to="/benchmark">
        <BenchIcon {...ic} /> Benchmark
      </NavLink>
      <div className="sect">Admin</div>
      <NavLink to="/agents">
        <Connect {...ic} /> Connected agents
      </NavLink>
      <NavLink to="/settings">
        <SettingsOption {...ic} /> Settings
      </NavLink>
      <div className="grow" />
      <div className="who">
        <Robot size="14px" color="#8FA79C" aria-hidden />
        <span>{who.user ?? "—"}</span>
        {who.mode === "local" && <button onClick={onSignOut}>Sign out</button>}
      </div>
      <div className="foot">{DISCLAIMER}</div>
    </nav>
  );
}

export default function App() {
  const [who, setWho] = useState<Who | null>(null);
  const [panel, setPanel] = useState<{ open: boolean; seed?: string; n: number }>({ open: false, n: 0 });
  const nav = useNavigate();
  const loc = useLocation();

  const refresh = useCallback(() => {
    api<Who>("/api/whoami").then(setWho).catch(() => setWho(null));
  }, []);
  useEffect(refresh, [refresh]);
  useEffect(() => {
    const h = () => refresh();
    window.addEventListener("shimpaku:unauth", h);
    return () => window.removeEventListener("shimpaku:unauth", h);
  }, [refresh]);

  const openAssistant = useCallback((q?: string) => setPanel((p) => ({ open: true, seed: q, n: p.n + 1 })), []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPanel((p) => ({ open: !p.open || !!p.seed, n: p.n + 1 }));
      } else if (e.key === "Escape") setPanel((p) => ({ ...p, open: false }));
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  // ?ask=… deep link (used for screenshots and shareable questions)
  const asked = useRef(false);
  useEffect(() => {
    const q = new URLSearchParams(loc.search).get("ask");
    if (q && !asked.current) {
      asked.current = true;
      openAssistant(q);
    }
  }, []); // eslint-disable-line

  if (!who) return <Loading label="Starting" />;
  if (who.mode === "local" && !who.authenticated) {
    return (
      <Routes>
        <Route path="/login" element={<Login onDone={() => { refresh(); nav("/sle"); }} />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }
  const signOut = () => api("/api/logout", { method: "POST" }).finally(refresh);

  return (
    <SessionCtx.Provider value={{ who, refresh, openAssistant }}>
      <a href="#main" className="sr-only">Skip to content</a>
      <div className={`shell${panel.open ? " with-panel" : ""}`}>
        <Nav who={who} onSignOut={signOut} />
        <main className="main" id="main">
          <Routes>
            <Route path="/" element={<Navigate to="/sle" replace />} />
            <Route path="/login" element={<Navigate to="/sle" replace />} />
            <Route path="/sle" element={<ServiceLevels />} />
            <Route path="/root-cause" element={<RootCauseIndex />} />
            <Route path="/root-cause/:id" element={<RootCause />} />
            <Route path="/actions" element={<Actions />} />
            <Route path="/actions/:id" element={<ActionDetail />} />
            <Route path="/benchmark" element={<Benchmark />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<div className="content"><h1>Not found</h1><p className="weak">No page at {loc.pathname}.</p></div>} />
          </Routes>
        </main>
        {panel.open && <AssistantPanel key={panel.n} seed={panel.seed} onClose={() => setPanel((p) => ({ ...p, open: false, seed: undefined }))} />}
      </div>
    </SessionCtx.Provider>
  );
}
