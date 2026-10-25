// Shim's reasoning traces: the last N answers of this browser tab, in memory only (no storage,
// nothing sent anywhere). A reload or a new tab starts empty.
import { useSyncExternalStore } from "react";

export type TraceStep = { name: string; call: string; args: string; ms?: number; ok?: boolean; ids: string[] };
export type TraceEvidence = { title: string; text: string; href?: string };
export type Trace = { id: number; q: string; at: number; page: string; model?: string; steps: TraceStep[]; evidence: TraceEvidence[]; text: string; done: boolean; error?: string };

export const MAX_TRACES = 20;
let traces: Trace[] = [];
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());

export function putTrace(t: Trace) {
  const i = traces.findIndex((x) => x.id === t.id);
  traces = i >= 0 ? traces.map((x) => (x.id === t.id ? t : x)) : [...traces, t].slice(-MAX_TRACES);
  emit();
}
export function clearTraces() {
  traces = [];
  emit();
}
let seq = 0;
export const nextTraceId = () => ++seq;

export function useTraces(): Trace[] {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => traces,
    () => traces,
  );
}

/** Where a tool's data lives in the dashboard. */
export function toolHref(name: string): string | null {
  if (name.startsWith("sle_get_blast") || name.startsWith("incident")) return "/root-cause";
  if (name.startsWith("sle_")) return "/sle";
  if (name.startsWith("action_")) return "/actions";
  if (name.startsWith("audit")) return "/audit";
  if (/^(topo_|routing_|device_|iface_|port_|inventory|config_)/.test(name)) return "/topology";
  if (name.startsWith("bench")) return "/benchmark";
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
