import type { ReactNode } from "react";
import { Chat } from "grommet-icons";
import { useSession } from "../session";
import { DemoBadge } from "./ui";

export default function TopBar({ title, crumbs, children }: { title: ReactNode; crumbs?: ReactNode; children?: ReactNode }) {
  const { who, openAssistant } = useSession();
  const mac = typeof navigator !== "undefined" && /Mac/.test(navigator.platform);
  return (
    <header className="topbar">
      <div>
        {crumbs && <div className="crumbs">{crumbs}</div>}
        <h1>{title}</h1>
      </div>
      {children}
      <span className="spacer" />
      <DemoBadge on={who.demo} />
      {who.mode === "off" && <span className="badge-mode" title="SHIMPAKU_AUTH_MODE=off: no sign-in; approvals still need a click">Auth off · no sign-in</span>}
      <button className="btn" onClick={() => openAssistant()} aria-keyshortcuts="Control+K Meta+K">
        <Chat size="15px" color="#00785C" aria-hidden /> Ask Shim <span className="kbd">{mac ? "⌘" : "Ctrl"} K</span>
      </button>
    </header>
  );
}
