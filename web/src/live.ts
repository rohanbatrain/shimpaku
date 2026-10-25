// Live data for the topology traffic overlay, the Junos event stream and time travel (/api/live/*).
import { useEffect, useRef, useState } from "react";
import { api, useApi, useSse } from "./api";

export type TrafficLink = {
  id: string; a: string; a_if: string | null; b: string; b_if: string | null;
  ab_bps: number | null; ba_bps: number | null; sample_t: number | null; stale: boolean;
  speed_bps: number | null; util_ab: number | null; util_ba: number | null; reason: string | null;
};
export type Traffic = {
  generated_at: number; live: boolean; t: number | null; at?: number; available?: boolean; reason?: string;
  speed_bps: number | null; speed_reason?: string; source?: string; links: TrafficLink[]; demo?: boolean;
};
export type JEvent = {
  id: number; t: number | null; ingest_t: number | null; device: string; interface: string | null; type: string;
  kind: string; severity: "error" | "warning" | "info"; message: string; attrs: Record<string, string>;
};
export type EventsPage = { events: JEvent[]; last_id: number | null; stored?: number; first_t?: number | null; source?: string; available?: boolean; reason?: string; demo?: boolean };
export type AsOf = {
  t: number; minute: number; available?: boolean; reason?: string;
  links: { id: string; state: "up" | "degraded" | "down" | "unknown"; good: string[]; bad: string[]; evidence: Record<string, string | null> }[];
  nodes: { name: string; state: "up" | "degraded" | "down" | "unknown"; collector_connected: boolean | null; verdict: { status: string; classifier: string | null; sub_classifier: string | null } | null }[];
  sles: { site: string; id: string; name: string; value_pct: number | null; n: number | null }[];
  traffic: { at: number | null; speed_bps: number | null; links: TrafficLink[] };
  notes: string[]; demo?: boolean;
};
export type Range = { from: number | null; now: number; max_history_s: number; retention: string; samples: { first: number | null; last: number | null; interval_s: number } };

export function fmtBps(v: number | null | undefined) {
  if (v == null) return "—";
  const u = ["bit/s", "kbit/s", "Mbit/s", "Gbit/s"];
  let i = 0, x = v;
  while (x >= 1000 && i < u.length - 1) { x /= 1000; i++; }
  return `${x >= 100 || i === 0 ? Math.round(x) : x.toFixed(1)} ${u[i]}`;
}

/** Live traffic: one GET, then the SSE stream (every ~5 s). Pass `t` to read a past minute instead. */
export function useTraffic(enabled = true) {
  const [d, setD] = useState<Traffic>();
  useEffect(() => { if (enabled) api<Traffic>("/api/live/traffic").then(setD).catch(() => {}); }, [enabled]);
  useSse<Traffic>(enabled ? "/api/live/stream/traffic" : null, "traffic", setD);
  return d;
}

/** Newest-first live events, bounded to `cap`; `paused` stops showing new ones (they are counted). */
export function useEventStream(cap = 50, paused = false) {
  const [rows, setRows] = useState<JEvent[]>([]);
  const [held, setHeld] = useState<JEvent[]>([]);
  const [after, setAfter] = useState<number | null>(null);
  const [state, setState] = useState<{ available: boolean; reason?: string; demo?: boolean; loaded: boolean }>({ available: true, loaded: false });
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  useEffect(() => {
    api<EventsPage>(`/api/live/events?limit=${cap}`).then((p) => {
      setState({ available: p.available !== false, reason: p.reason, demo: p.demo, loaded: true });
      setRows(p.events ?? []);
      setAfter(p.last_id ?? 0);
    }).catch((e) => setState({ available: false, reason: String(e.message ?? e), loaded: true }));
  }, [cap]);
  useSse<JEvent[]>(after == null ? null : `/api/live/stream/events?after_id=${after}`, "events", (batch) => {
    const fresh = [...batch].sort((a, b) => b.id - a.id);
    if (pausedRef.current) setHeld((h) => [...fresh, ...h].slice(0, cap));
    else setRows((r) => dedupe([...fresh, ...r]).slice(0, cap));
  });
  useEffect(() => {
    if (!paused && held.length) { setRows((r) => dedupe([...held, ...r]).slice(0, cap)); setHeld([]); }
  }, [paused, held, cap]);
  return { rows, held: held.length, ...state };
}

export function dedupe(r: JEvent[]) {
  const seen = new Set<number>();
  return r.filter((e) => (seen.has(e.id) ? false : (seen.add(e.id), true)));
}

/** ?t= deep link for time travel: null = live. */
export function useAsOf(t: number | null) {
  return useApi<AsOf>(t == null ? null : `/api/live/at?t=${Math.floor(t)}`);
}

export function useRange() {
  return useApi<Range>("/api/live/range");
}
