import type { ReactNode } from "react";
import { Chat } from "grommet-icons";
import { useSession } from "../session";
import { DemoBadge, ReplayBadge } from "./ui";

// these titles repeat the mobile app bar, so they are visually hidden there (kept for screen readers)
const SECTIONS = new Set(["Overview", "Topology", "Service levels", "Root cause", "Actions", "Benchmark", "Connected agents", "Settings"]);

export default function TopBar({ title, crumbs, children }: { title: ReactNode; crumbs?: ReactNode; children?: ReactNode }) {
  const { who, openAssistant } = useSession();
  const mac = typeof navigator !== "undefined" && /Mac/.test(navigator.platform);
  return (
    <header className="topbar">
      <div>
        {crumbs && <div className="crumbs">{crumbs}</div>}
        <h1 className={SECTIONS.has(title as string) ? "dup" : undefined}>{title}</h1>
      </div>
      {children && <div className="topbar-ctl">{children}</div>}
      <span className="spacer" />
      <DemoBadge on={who.demo} />
      <ReplayBadge replay={who.replay} />
      {who.role === "visitor" && !who.replay && (
        <>
          <span className="badge-view" title="Public view: live data, nothing can be changed">Viewing read-only</span>
          <a className="btn primary req-cta" href="/request">Request a live session</a>
        </>
      )}
      {who.mode === "off" && !who.replay && <span className="badge-mode" title="SHIMPAKU_AUTH_MODE=off: no sign-in; approvals still need a click">Auth off · no sign-in</span>}
      <button className="btn topbar-ask" onClick={() => openAssistant()} aria-keyshortcuts="Control+K Meta+K">
        <Chat size="15px" color="#00785C" aria-hidden /> Ask Shim <span className="kbd">{mac ? "⌘" : "Ctrl"} K</span>
      </button>
    </header>
  );
}
