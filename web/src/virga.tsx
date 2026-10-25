// Virga live: one shared connection to the fault-drill state (docs/build/virga-live.md §2).
// A single EventSource on /api/virga/stream feeds every page through this context; if SSE fails the
// provider polls /api/virga/state every 5 s instead. A 404 means the feature is not on this server:
// `state` stays null and every consumer renders nothing.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export type PhaseName = "preflight" | "inject" | "verify_injected" | "hold" | "revert" | "verify_reverted" | "done";
export type PhaseStatus = "pending" | "running" | "ok" | "failed" | "skipped";

export type VPhase = { name: PhaseName; status: PhaseStatus; t_start: number | null; t_end: number | null; detail: string };
export type VTrial = {
  id: string; scenario: string; title: string; layer: string; site: string; nodes: string[]; links: [string, string][]; by_role?: string | null;
  t_start: number; hold_until: number | null; phase: PhaseName; verdict: null | "pass" | "fail";
  phases: VPhase[]; log: { t: number; msg: string }[];
};
export type VRecent = {
  id: string; scenario: string; title: string; t_start: number; t_end: number | null; verdict: null | "pass" | "fail";
  time_to_effect_s: number | null; time_to_restore_s: number | null; incident_opened: boolean;
};
export type VState = {
  enabled: boolean; reason: string | null; simulated: boolean; busy: boolean; needs_recovery: boolean;
  trial: VTrial | null; recent: VRecent[];
};
export type VScenario = {
  id: string; title: string; layer: string; site: string; nodes: string[]; links: [string, string][];
  impact: "user" | "latent"; demo_ok: boolean; why_not: string | null; est_s: number;
  selftest: { passes: number; runs: number; last_at: number | null };
};
export type VCatalogue = {
  enabled: boolean; reason: string | null; simulated: boolean; hold: { default_s: number; max_s: number }; scenarios: VScenario[];
};

export const PHASE_LABEL: Record<PhaseName, string> = {
  preflight: "Preflight", inject: "Inject", verify_injected: "Fault verified", hold: "Hold",
  revert: "Revert", verify_reverted: "Revert verified", done: "Done",
};

/** A drill is live from preflight until the runner reports `done`. */
export const isLive = (t?: VTrial | null): t is VTrial => !!t && t.phase !== "done";

export function clock(s: number) {
  s = Math.max(0, Math.floor(s));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
export function secs(s?: number | null) {
  if (s == null) return "—";
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 90) return `${Math.round(s)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

type Conn = "connecting" | "sse" | "polling" | "off";
type Ctx = { state: VState | null; conn: Conn; reload: () => void; skew: React.MutableRefObject<number> };
const VirgaCtx = createContext<Ctx>({ state: null, conn: "off", reload: () => {}, skew: { current: 0 } });

export function VirgaProvider({ children, live = true }: { children: ReactNode; live?: boolean }) {
  const [state, setState] = useState<VState | null>(null);
  const [conn, setConn] = useState<Conn>("connecting");
  const skew = useRef(0); // server clock minus this browser's clock, seconds
  const pullRef = useRef<() => Promise<void>>(async () => {});

  useEffect(() => {
    let dead = false;
    let es: EventSource | null = null;
    let poll: number | undefined;
    let retry: number | undefined;

    const stop = () => {
      es?.close(); es = null;
      if (poll) window.clearInterval(poll);
      if (retry) window.clearTimeout(retry);
      poll = retry = undefined;
    };
    const gone = () => { stop(); if (!dead) { setState(null); setConn("off"); } };

    // returns false when the feature is absent on this server
    const pull = async (): Promise<boolean> => {
      try {
        const r = await fetch("/api/virga/state", { credentials: "same-origin", headers: { Accept: "application/json" } });
        if (dead) return true;
        if (r.status === 404) return false;
        if (!r.ok) return true;
        const d = r.headers.get("Date");
        if (d && !Number.isNaN(Date.parse(d))) skew.current = Date.parse(d) / 1000 - Date.now() / 1000;
        setState(await r.json());
      } catch { /* network blip: keep the last state */ }
      return true;
    };
    pullRef.current = async () => { if (!(await pull())) gone(); };

    const startPolling = () => {
      if (poll || dead) return;
      setConn("polling");
      poll = window.setInterval(() => { if (document.visibilityState === "visible") void pullRef.current(); }, 5000);
      retry = window.setTimeout(() => { // try the stream again after a minute
        if (poll) window.clearInterval(poll);
        poll = retry = undefined;
        openSse();
      }, 60000);
    };
    const openSse = () => {
      if (dead || typeof EventSource === "undefined") { startPolling(); return; }
      es = new EventSource("/api/virga/stream", { withCredentials: true });
      es.onopen = () => setConn("sse");
      es.addEventListener("state", (e) => {
        try { setState(JSON.parse((e as MessageEvent).data)); setConn("sse"); } catch { /* ignore a bad frame */ }
      });
      es.onerror = () => { es?.close(); es = null; startPolling(); void pullRef.current(); };
    };

    void (async () => {
      const ok = await pull();
      if (dead) return;
      if (!ok) { gone(); return; }
      if (!live) { setConn("off"); return; } // replay: one read, no stream
      openSse();
    })();
    return () => { dead = true; stop(); };
  }, [live]);

  const reload = useCallback(() => { void pullRef.current(); }, []);
  const value = useMemo(() => ({ state, conn, reload, skew }), [state, conn, reload]);
  return <VirgaCtx.Provider value={value}>{children}</VirgaCtx.Provider>;
}

export const useVirga = () => useContext(VirgaCtx);

/** Server-adjusted "now" in seconds, ticking once a second while `active`. */
export function useServerNow(active: boolean) {
  const { skew } = useVirga();
  const [now, setNow] = useState(() => Date.now() / 1000 + skew.current);
  useEffect(() => {
    if (!active) return;
    const tick = () => setNow(Date.now() / 1000 + skew.current);
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [active, skew]);
  return now;
}

/** Nodes and links a live drill touches, for the topology marks (null when none is live). */
export function useFaultMarks(): { nodes: string[]; links: [string, string][] } | null {
  const { state } = useVirga();
  const t = state?.trial;
  return useMemo(() => (isLive(t) ? { nodes: t.nodes ?? [], links: t.links ?? [] } : null), [t]);
}
