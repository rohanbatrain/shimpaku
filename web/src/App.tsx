import AccessBanner from "./components/AccessBanner";
import DrillBanner from "./components/DrillBanner";
import { VirgaProvider } from "./virga";
import { type RefObject, lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router";
import { Chat, Menu, Close, Robot, Dashboard, Tree, Home, Cluster, Action, BarChart as BenchIcon, Connect, SettingsOption, Calendar, Terminal, Monitor, Cloud, Shield, Link as ChainIcon, Play, Servers, Bug } from "grommet-icons";
import { api, useApi } from "./api";
import { SessionCtx, signOutAndClear, type Who } from "./session";
import { Loading } from "./components/ui";
import "./requests.css";
import { Notification as EventsIcon } from "grommet-icons";
const AssistantPanel = lazy(() => import("./components/AssistantPanel"));
const ServiceLevels = lazy(() => import("./pages/ServiceLevels"));
const Overview = lazy(() => import("./pages/Overview"));
const Topology = lazy(() => import("./pages/Topology"));
const Events = lazy(() => import("./pages/Events"));
const Device = lazy(() => import("./pages/Device"));
const RootCause = lazy(() => import("./pages/RootCause"));
const RootCauseIndex = lazy(() => import("./pages/RootCause").then((m) => ({ default: m.RootCauseIndex })));
const Actions = lazy(() => import("./pages/Actions"));
const ActionDetail = lazy(() => import("./pages/ActionDetail"));
const Benchmark = lazy(() => import("./pages/Benchmark"));
const Login = lazy(() => import("./pages/Login"));
const Start = lazy(() => import("./pages/Start"));
const LeaseBanner = lazy(() => import("./components/Lease").then((m) => ({ default: m.LeaseBanner })));
const Agents = lazy(() => import("./pages/Agents"));
const Settings = lazy(() => import("./pages/Settings"));
const Request = lazy(() => import("./pages/Request"));
const RequestStatus = lazy(() => import("./pages/RequestStatus"));
const SessionRequests = lazy(() => import("./pages/SessionRequests"));
const Console = lazy(() => import("./pages/Console"));
const OpsHost = lazy(() => import("./pages/OpsHost"));
const OpsGateway = lazy(() => import("./pages/OpsGateway"));
const Probes = lazy(() => import("./pages/Probes"));
const Architecture = lazy(() => import("./pages/Architecture"));
const Guardrails = lazy(() => import("./pages/Guardrails"));
const Audit = lazy(() => import("./pages/Audit"));
const AssistantTrace = lazy(() => import("./pages/AssistantTrace"));
const Theatre = lazy(() => import("./pages/Theatre"));
const Hardware = lazy(() => import("./pages/Hardware"));
const Virga = lazy(() => import("./pages/Virga"));

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

const TITLES: [string, string][] = [["/topology", "Topology"], ["/hardware", "Hardware"], ["/devices", "Device"], ["/sle", "Service levels"], ["/root-cause", "Root cause"], ["/actions", "Actions"], ["/theatre", "Theatre"], ["/virga", "Fault lab"], ["/benchmark", "Benchmark"], ["/agents", "Connected agents"], ["/settings", "Settings"], ["/admin/requests", "Session requests"], ["/console", "Console"], ["/ops/host", "Lab host"], ["/ops/gateway", "LLM gateway"], ["/probes", "Probe heatmap"], ["/architecture", "Architecture"], ["/guardrails", "Guardrails"], ["/audit", "Audit chain"], ["/assistant/trace", "Shim's reasoning"]];
const MOBILE = "(max-width: 900px)";

// ≤ 900 px: compact app bar; the nav becomes a focus-trapped slide-over drawer.
function AppBar({ open, onToggle, btn, onSignOut }: { open: boolean; onToggle: () => void; btn: RefObject<HTMLButtonElement | null>; onSignOut?: () => void }) {
  const loc = useLocation();
  const title = TITLES.find(([p]) => loc.pathname.startsWith(p))?.[1] ?? (loc.pathname === "/" ? "Overview" : "Shimpaku");
  return (
    <div className="appbar">
      <Mark size={28} />
      <span className="appbar-t">{title}</span>
      {onSignOut && <button type="button" className="appbar-signout" onClick={onSignOut}>Sign out</button>}
      <button ref={btn} type="button" className="appbar-menu" onClick={onToggle} aria-expanded={open} aria-controls="main-nav" aria-label={open ? "Close menu" : "Open menu"}>
        {open ? <Close size="20px" color="currentColor" aria-hidden /> : <Menu size="20px" color="currentColor" aria-hidden />}
      </button>
    </div>
  );
}

function Nav({ who, onSignOut, drawer, onClose }: { who: Who; onSignOut: () => void; drawer: boolean; onClose: () => void }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!drawer) return;
    const el = ref.current;
    const items = () => Array.from(el?.querySelectorAll<HTMLElement>("a[href], button") ?? []);
    items()[0]?.focus();
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
      if (e.key !== "Tab") return;
      const f = items(); if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", k, true);
    return () => document.removeEventListener("keydown", k, true);
  }, [drawer, onClose]);
  const acts = useApi<{ actions: { status: string }[] }>("/api/actions", { url: "/api/stream/actions", event: "actions" });
  const open = acts.data?.actions.filter((a) => ["proposed", "awaiting_approval"].includes(a.status)).length ?? 0;
  const op = who.role === "operator" && !who.presenting; // presenter mode: no admin nav
  const reqs = useApi<{ pending: number }>(op && who.mode === "local" ? "/api/admin/session-requests?status=pending" : null);
  useEffect(() => {
    if (!op || who.mode !== "local") return;
    const t = setInterval(reqs.reload, 30000);
    window.addEventListener("shimpaku:requests", reqs.reload); // a decision on the Session requests page
    return () => { clearInterval(t); window.removeEventListener("shimpaku:requests", reqs.reload); };
  }, [op, who.mode, reqs.reload]);
  const pending = reqs.data?.pending ?? 0;
  const ic = { size: "16px", color: "currentColor" } as const;
  return (
    <nav ref={ref} id="main-nav" className={`nav${drawer ? " open" : ""}`} aria-label="Main">
      <div className="brand">
        <Mark />
        <div>
          Shimpaku
          <small>home-lab assurance</small>
        </div>
      </div>
      <div className="sect">Monitor</div>
      <NavLink to="/" end>
        <Home {...ic} /> Overview
      </NavLink>
      <NavLink to="/topology">
        <Cluster {...ic} /> Topology
      </NavLink>
      <NavLink to="/hardware">
        <Servers {...ic} /> Hardware
      </NavLink>
      <NavLink to="/events">
        <EventsIcon {...ic} /> Events
      </NavLink>
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
      {who.role === "operator" && (
        <NavLink to="/console">
          <Terminal {...ic} /> Console
        </NavLink>
      )}
      <NavLink to="/theatre">
        <Play {...ic} /> Theatre
      </NavLink>
      <NavLink to="/virga">
        <Bug {...ic} /> Fault lab
      </NavLink>
      <div className="sect">Explain</div>
      <NavLink to="/guardrails">
        <Shield {...ic} /> Guardrails
      </NavLink>
      <NavLink to="/audit">
        <ChainIcon {...ic} /> Audit chain
      </NavLink>
      <NavLink to="/assistant/trace">
        <Robot {...ic} /> Shim's reasoning
      </NavLink>
      <div className="sect">Evaluate</div>
      <NavLink to="/benchmark">
        <BenchIcon {...ic} /> Benchmark
      </NavLink>
      <NavLink to="/architecture">
        <Tree {...ic} /> Architecture
      </NavLink>
      {op && (
        <>
          <div className="sect">Admin</div>
          <NavLink to="/agents">
            <Connect {...ic} /> Connected agents
          </NavLink>
          <NavLink to="/settings">
            <SettingsOption {...ic} /> Settings
          </NavLink>
          <NavLink to="/ops/host">
            <Monitor {...ic} /> Lab host
          </NavLink>
          <NavLink to="/ops/gateway">
            <Cloud {...ic} /> LLM gateway
          </NavLink>
          {who.mode === "local" && (
            <NavLink to="/admin/requests">
              <Calendar {...ic} /> Session requests
              {pending > 0 && <span className="count" aria-label={`${pending} pending`}>{pending}</span>}
            </NavLink>
          )}
        </>
      )}
      <div className="grow" />
      <div className="who">
        <Robot size="14px" color="#8FA79C" aria-hidden />
        <span>{who.user ?? "—"}</span>
        {who.mode === "local" && op && <button className="sign-out" onClick={onSignOut}>Sign out</button>}
        {who.role === "visitor" && <button onClick={() => location.assign("/login")}>Operator sign in</button>}
      </div>
      <div className="foot">{DISCLAIMER}</div>
    </nav>
  );
}

const MAC = typeof navigator !== "undefined" && /Mac/.test(navigator.platform);

// Floating launcher for the assistant panel: eager shell, the panel itself stays lazy.
// Hidden while the panel is open (the panel has its own close control).
function AskBubble({ onOpen }: { onOpen: () => void }) {
  const tip = `Ask Shim ${MAC ? "⌘" : "Ctrl+"}K`;
  return (
    <button type="button" className="ask-bubble" onClick={onOpen} aria-label="Ask Shim" title={tip} aria-keyshortcuts="Control+K Meta+K">
      <Chat size="24px" color="currentColor" aria-hidden />
      <span className="ask-bubble-tip" aria-hidden>{tip}</span>
    </button>
  );
}

export default function App() {
  const [who, setWho] = useState<Who | null>(null);
  const [panel, setPanel] = useState<{ open: boolean; seed?: string; n: number }>({ open: false, n: 0 });
  const nav = useNavigate();
  const loc = useLocation();
  const [drawer, setDrawer] = useState(false);
  const menuBtn = useRef<HTMLButtonElement>(null);
  const closeDrawer = useCallback(() => { setDrawer(false); menuBtn.current?.focus(); }, []);
  useEffect(() => setDrawer(false), [loc.pathname]);
  useEffect(() => {
    const mq = window.matchMedia(MOBILE);
    const h = () => !mq.matches && setDrawer(false);
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, []);

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
  // a full load: the new tester session cookie replaces whatever router state existed
  const started = () => location.replace("/actions");
  // public pages: no sign-in, outside the app shell
  if (loc.pathname === "/request" || loc.pathname === "/request/status") {
    return <Suspense fallback={<Loading label="Loading" />}>{loc.pathname === "/request" ? <Request /> : <RequestStatus />}</Suspense>;
  }
  if (loc.pathname === "/login" && who.role === "visitor") {
    return <Suspense fallback={<Loading label="Loading" />}><Login onDone={() => location.assign("/sle")} /></Suspense>;
  }
  if (loc.pathname === "/start" && who.role !== "tester") {
    return <Suspense fallback={<Loading label="Loading" />}><Start onDone={started} /></Suspense>;
  }
  if (who.mode === "local" && !who.authenticated) {
    return (
      <Suspense fallback={<Loading label="Loading" />}>
      <Routes>
        <Route path="/login" element={<Login onDone={() => { refresh(); nav("/sle"); }} />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
      </Suspense>
    );
  }
  const signOut = () => { void signOutAndClear(); };

  return (
    <SessionCtx.Provider value={{ who, refresh, openAssistant }}>
      <VirgaProvider live={!who.replay}>
      <a href="#main" className="sr-only">Skip to content</a>
      <div className={`shell${panel.open ? " with-panel" : ""}${drawer ? " drawer-open" : ""}`}>
        <AppBar open={drawer} onToggle={() => (drawer ? closeDrawer() : setDrawer(true))} btn={menuBtn}
          onSignOut={who.mode === "local" && who.role === "operator" ? signOut : undefined} />
        <Nav who={who} onSignOut={signOut} drawer={drawer} onClose={closeDrawer} />
        {drawer && <div className="scrim" onClick={closeDrawer} aria-hidden />}
        <main className="main" id="main">
          <AccessBanner />
          <DrillBanner />
          {who.role === "tester" && <Suspense fallback={null}><LeaseBanner /></Suspense>}
          <Suspense fallback={<Loading label="Loading" />}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/topology" element={<Topology />} />
            <Route path="/hardware" element={<Hardware />} />
            <Route path="/events" element={<Events />} />
            <Route path="/devices/:name" element={<Device />} />
            <Route path="/login" element={<Navigate to="/sle" replace />} />
            <Route path="/start" element={<Navigate to="/actions" replace />} />
            <Route path="/sle" element={<ServiceLevels />} />
            <Route path="/root-cause" element={<RootCauseIndex />} />
            <Route path="/root-cause/:id" element={<RootCause />} />
            <Route path="/actions" element={<Actions />} />
            <Route path="/actions/:id" element={<ActionDetail />} />
            <Route path="/theatre" element={<Theatre />} />
            <Route path="/virga" element={<Virga />} />
            <Route path="/benchmark" element={<Benchmark />} />
            <Route path="/probes" element={<Probes />} />
            <Route path="/architecture" element={<Architecture />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/admin/requests" element={<SessionRequests />} />
            <Route path="/console" element={<Console />} />
            <Route path="/ops/host" element={<OpsHost />} />
            <Route path="/ops/gateway" element={<OpsGateway />} />
            <Route path="/guardrails" element={<Guardrails />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/assistant/trace" element={<AssistantTrace />} />
            <Route path="*" element={<div className="content"><h1>Not found</h1><p className="weak">No page at {loc.pathname}.</p></div>} />
          </Routes>
          </Suspense>
        </main>
        {!panel.open && <AskBubble onOpen={() => openAssistant()} />}
        {panel.open && <Suspense fallback={null}><AssistantPanel key={panel.n} seed={panel.seed} onClose={() => setPanel((p) => ({ ...p, open: false, seed: undefined }))} /></Suspense>}
      </div>
      </VirgaProvider>
    </SessionCtx.Provider>
  );
}
