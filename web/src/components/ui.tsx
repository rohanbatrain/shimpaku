import { Spinner } from "grommet";
import { Alert, StatusGood, StatusInfo, StatusWarning, CircleQuestion } from "grommet-icons";
import type { ReactNode } from "react";
import { STATUS_TONE, statusLabel, type Tone } from "../format";
import type { Loadable } from "../api";

export function Pill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`pill ${tone}`}>
      <span className="dot" aria-hidden />
      {children}
    </span>
  );
}

export function StatusPill({ status }: { status?: string | null }) {
  const label = statusLabel(status);
  return <Pill tone={STATUS_TONE[label] ?? "neutral"}>{label}</Pill>;
}

export function ToneIcon({ tone, size = "16px" }: { tone: Tone; size?: string }) {
  const color = { ok: "#1B7F3A", warn: "#8A5B00", crit: "#B3261E", info: "#1D6E8C", neutral: "#6F7B76" }[tone];
  const I = { ok: StatusGood, warn: StatusWarning, crit: Alert, info: StatusInfo, neutral: CircleQuestion }[tone];
  return <I color={color} size={size} aria-hidden />;
}

export function Loading({ label = "Loading…", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" style={{ display: "flex", flexDirection: "column", gap: 10, padding: 16 }}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skel" style={{ height: 22, width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  return (
    <div className="state error" role="alert">
      <Alert color="#B3261E" />
      <b>Could not load this.</b>
      <span className="small">{error.message}</span>
      {retry && (
        <button className="btn" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state">
      <StatusGood color="#1B7F3A" />
      <b style={{ color: "var(--t)" }}>{title}</b>
      {children && <span className="small">{children}</span>}
    </div>
  );
}

/** Render loading / error / empty around a Loadable. */
export function Load<T>({ q, empty, children, rows }: { q: Loadable<T>; empty?: (d: T) => ReactNode | null; children: (d: T) => ReactNode; rows?: number }) {
  if (q.error && !q.data) return <ErrorState error={q.error} retry={q.reload} />;
  if (!q.data) return <Loading rows={rows} />;
  const d = q.data as any;
  if (d && d.available === false)
    return (
      <div className="state">
        <b style={{ color: "var(--t)" }}>Not collected yet</b>
        <span className="small">{d.reason ?? "This data source is not running."}</span>
      </div>
    );
  const e = empty?.(q.data);
  if (e) return <>{e}</>;
  return <>{children(q.data)}</>;
}

export function Busy() {
  return <Spinner size="xsmall" color="#01A982" a11yTitle="Working" />;
}

export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function DemoBadge({ on }: { on?: boolean }) {
  return on ? (
    <span className="badge-demo" title="Canned data shaped like the lab; no device is contacted">
      Demo data
    </span>
  ) : null;
}

export function Diff({ text }: { text: string }) {
  return (
    <div className="diff" role="region" aria-label="Configuration diff" tabIndex={0}>
      {text.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("[") ? "ctx" : ""}>
          {l || " "}
        </div>
      ))}
    </div>
  );
}

/** Minimal markdown: **bold** only (assistant answers). */
export function Rich({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return <>{parts.map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <b key={i}>{p.slice(2, -2)}</b> : <span key={i}>{p}</span>))}</>;
}
