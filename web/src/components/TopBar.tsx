import type { ReactNode } from "react";
import { Chat } from "grommet-icons";
import { api } from "../api";
import { useSession } from "../session";
import "../console.css";
import QuickLink from "./QuickLink";
import Info from "./Info";
import { useLocation } from "react-router";
import { pageExplainer } from "../glossary";
import { DemoBadge, ReplayBadge } from "./ui";

// these titles repeat the mobile app bar, so they are visually hidden there (kept for screen readers)
const SECTIONS = new Set(["Overview", "Topology", "Hardware", "Service levels", "Root cause", "Actions", "Benchmark", "Virga", "Connected agents", "Settings", "Lab host", "LLM gateway", "Probe heatmap", "Architecture", "Deploy your own"]);

export default function TopBar({ title, crumbs, children }: { title: ReactNode; crumbs?: ReactNode; children?: ReactNode }) {
  const { who, openAssistant, refresh } = useSession();
  const present = (on: boolean) => { api("/api/console/presenting", { json: { on } }).then(refresh).catch(() => {}); };
  const path = useLocation().pathname;
  const about = pageExplainer(path);
  const mac = typeof navigator !== "undefined" && /Mac/.test(navigator.platform);
  return (
    <header className="topbar">
      <div>
        {crumbs && <div className="crumbs">{crumbs}</div>}
        <div className="topbar-t">
          <h1 className={SECTIONS.has(title as string) ? "dup" : undefined}>{title}</h1>
          {about && <Info e={about} label="About this page" />}
        </div>
      </div>
      {children && <div className="topbar-ctl">{children}</div>}
      <span className="spacer" />
      <DemoBadge on={who.demo} />
      <ReplayBadge replay={who.replay} />
      {/* visitors: the read-only notice and both ways in live in AccessBanner, once per page */}
      {who.role === "operator" && !who.replay && <QuickLink />}
      {who.role === "operator" && !who.replay && (who.presenting
        ? <><span className="badge-presenting" title="Presenter mode: the visitor view, with your operator console">Presenting</span>
            <button type="button" className="btn present-toggle" onClick={() => present(false)}>Stop presenting</button></>
        : <button type="button" className="btn present-toggle" onClick={() => present(true)} title="Switch to the clean visitor-style view for a demo">Presenter mode</button>)}
      {who.mode === "off" && !who.replay && !who.presenting && <span className="badge-mode" title="SHIMPAKU_AUTH_MODE=off: no sign-in; approvals still need a click">Auth off · no sign-in</span>}
      {path !== "/shim" && <button className="btn topbar-ask" onClick={() => openAssistant()} aria-keyshortcuts="Control+K Meta+K">
        <Chat size="15px" color="#00785C" aria-hidden /> Ask Shim <span className="kbd">{mac ? "⌘" : "Ctrl"} K</span>
      </button>}
    </header>
  );
}
