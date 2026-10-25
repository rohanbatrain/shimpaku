// Shim's reasoning traces: every answer in this browser's saved Shim conversations (shim.ts), as
// steps. Nothing is sent anywhere; signing out clears them.
import { useMemo } from "react";
import { useShim, type Turn } from "./shim";

export type TraceStep = { name: string; call: string; args: string; ms?: number; ok?: boolean; ids: string[] };
export type TraceEvidence = { title: string; text: string; href?: string };
export type Trace = { id: number; q: string; at: number; page: string; model?: string; steps: TraceStep[]; evidence: TraceEvidence[]; text: string; done: boolean; error?: string; convo?: string };

export const MAX_TRACES = 30;

export function traceOf(t: Turn, convo?: string): Trace {
  return {
    id: t.id, q: t.q, at: t.at, page: t.page, model: t.model, text: t.text, done: t.done, error: t.error, evidence: t.evidence, convo,
    steps: t.tools.map((c) => ({ name: c.name, call: c.call, args: argsOf(c), ms: c.ms, ok: c.ok, ids: c.ids ?? [] })),
  };
}

export function useTraces(): Trace[] {
  const s = useShim();
  return useMemo(() => s.convos.flatMap((c) => c.turns.map((t) => traceOf(t, c.id))).sort((a, b) => a.at - b.at).slice(-MAX_TRACES), [s]);
}

/** Where a tool's data lives in the dashboard. */
export function toolHref(name: string): string | null {
  if (name.startsWith("sle_get_blast") || name.startsWith("incident")) return "/root-cause";
  if (name.startsWith("sle_")) return "/sle";
  if (name.startsWith("action_")) return "/actions";
  if (name.startsWith("audit")) return "/audit";
  if (/^(topo_|routing_|device_|iface_|port_|inventory|config_)/.test(name)) return "/topology";
  if (name.startsWith("bench")) return "/benchmark";
  if (name.startsWith("kb_")) return "/architecture";
  return null;
}

/** A result id → its page, when the id names something the dashboard shows. */
export function idHref(id: string): string | null {
  if (/^INC-/.test(id)) return `/root-cause/${encodeURIComponent(id)}`;
  if (/^act-/.test(id)) return `/actions/${encodeURIComponent(id)}`;
  if (/^(r\d|sw\d|leaf\d|spine\d)$/.test(id)) return `/devices/${id}`;
  return null;
}

/** Arguments as shown: the server's redacted summary, else what the call string carries. */
export function argsOf(e: { args?: string; call: string }): string {
  if (e.args) return e.args;
  const m = e.call.match(/\((.*)\)$/);
  return m ? m[1] : "";
}
