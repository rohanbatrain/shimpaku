export const STATUS_LABEL: Record<string, string> = {
  open: "Open",
  proposed: "Open",
  awaiting_approval: "Open",
  blocked: "Blocked",
  rejected: "Rejected",
  approved: "Running",
  committing: "Running",
  committed: "Running",
  validating: "Running",
  running: "Running",
  automatic: "Automatic",
  fixed_by_operator: "Fixed by operator",
  validated: "Verified",
  verified: "Verified",
  recurred: "Recurred",
  cancelled: "Cancelled",
  rolled_back: "Rolled back",
  failed: "Failed",
};
export type Tone = "ok" | "warn" | "crit" | "info" | "neutral";
export const STATUS_TONE: Record<string, Tone> = {
  Open: "crit",
  Running: "info",
  Automatic: "ok",
  "Fixed by operator": "ok",
  Verified: "ok",
  Recurred: "warn",
  Cancelled: "neutral",
  Blocked: "crit",
  Rejected: "neutral",
  "Rolled back": "warn",
  Failed: "crit",
};
export const statusLabel = (s?: string | null) => (s ? STATUS_LABEL[s] ?? s.replace(/_/g, " ") : "—");

export function time(t?: number | null) {
  if (!t) return "—";
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}
export function ago(t?: number | null) {
  if (!t) return "—";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
export const pct100 = (v?: number | null, d = 0) => (v == null ? "—" : `${v.toFixed(d)}%`);
export const ratio = (v?: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
export function sleTone(value: number, goal: number): Tone {
  if (value >= goal) return "ok";
  if (value >= goal - 10) return "warn";
  return "crit";
}
