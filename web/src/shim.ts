// Shim's conversations: kept in this browser only (localStorage, under a `shimpaku` key, so signing
// out wipes them), owned by the signed-in user, and independent of the chat window: closing the
// window does not stop an answer, and a reload or another tab sees the same history. The last few
// turns of the open conversation go with each question so Shim can follow up.
import { useSyncExternalStore } from "react";
import { ask, type AskEvent } from "./api";

export type Turn = {
  id: number; q: string; at: number; page: string; model?: string;
  tools: Extract<AskEvent, { type: "tool" }>[];
  evidence: Extract<AskEvent, { type: "evidence" }>[];
  text: string; done: boolean; error?: string; stopped?: boolean;
  last?: number; // when the latest stream event arrived (seconds), to tell "slow" from "stuck"
};
export type Convo = { id: string; title: string; at: number; turns: Turn[] };
type State = { owner: string | null; active: string | null; convos: Convo[] };

const KEY = "shimpaku.shim.v1";
const MAX_CONVOS = 25, MAX_TURNS = 40, HISTORY_TURNS = 6, HISTORY_CHARS = 1500;

let state: State = load();
// guide mode: a visitor's Shim (POST /api/assistant/public) only explains Shimpaku from its docs
let guide = false;
export function setGuide(on: boolean) { guide = on; }
const subs = new Set<() => void>();
const live = new Map<number, AbortController>();

function load(): State {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || "null");
    if (s && Array.isArray(s.convos)) {
      // an answer cut off by a reload is finished, not "answering" forever
      for (const c of s.convos) for (const t of c.turns) if (!t.done) Object.assign(t, { done: true, stopped: true });
      return s;
    }
  } catch {}
  return { owner: null, active: null, convos: [] };
}
let saveT: number | undefined;
function set(f: (s: State) => State) {
  state = f(state);
  subs.forEach((g) => g());
  window.clearTimeout(saveT);
  saveT = window.setTimeout(() => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch {} }, 250);
}
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => { if (e.key === KEY) { state = load(); subs.forEach((g) => g()); } });
}

export function useShim(): State {
  return useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => state, () => state);
}

/** A different signed-in user at this browser starts with an empty history. A visitor (no user,
 * e.g. after an operator session times out) claims nothing: the history waits for its owner. */
export function claim(user: string | null) {
  if (user && state.owner !== user) set(() => ({ owner: user, active: null, convos: [] }));
}

let seq = Date.now() % 1e6;
const nid = () => ++seq;
const upd = (cid: string, tid: number, f: (t: Turn) => Turn) =>
  set((s) => ({ ...s, convos: s.convos.map((c) => (c.id === cid ? { ...c, turns: c.turns.map((t) => (t.id === tid ? f(t) : t)) } : c)) }));

export function newChat() { set((s) => ({ ...s, active: null })); }
export function openChat(id: string) { set((s) => ({ ...s, active: id })); }
export function deleteChat(id: string) {
  set((s) => ({ ...s, active: s.active === id ? null : s.active, convos: s.convos.filter((c) => c.id !== id) }));
}
export function clearAll() { live.forEach((a) => a.abort()); set((s) => ({ ...s, active: null, convos: [] })); }
export const busyIn = (c?: Convo) => !!c && c.turns.some((t) => !t.done);
export const activeConvo = (s: State) => s.convos.find((c) => c.id === s.active);

export function stop(c?: Convo) {
  c?.turns.forEach((t) => { if (!t.done) { live.get(t.id)?.abort(); upd(c.id, t.id, (x) => ({ ...x, done: true, stopped: true })); } });
}

/** Ask in the open conversation (or a new one). Returns false while the conversation is still answering. */
export function send(question: string, page: string): boolean {
  const q = question.trim();
  if (!q) return false;
  let c = activeConvo(state);
  if (busyIn(c)) return false;
  if (!c) {
    c = { id: `c${nid()}`, title: q.length > 60 ? q.slice(0, 57) + "…" : q, at: Date.now() / 1000, turns: [] };
    const fresh = c;
    set((s) => ({ ...s, active: fresh.id, convos: [fresh, ...s.convos].slice(0, MAX_CONVOS) }));
  }
  const cid = c.id;
  const history = c.turns.filter((t) => t.done && t.text && !t.error).slice(-HISTORY_TURNS)
    .map((t) => ({ q: t.q.slice(0, HISTORY_CHARS), a: t.text.slice(0, HISTORY_CHARS) }));
  const t: Turn = { id: nid(), q, at: Date.now() / 1000, page, tools: [], evidence: [], text: "", done: false };
  set((s) => ({ ...s, convos: s.convos.map((x) => (x.id === cid ? { ...x, at: t.at, turns: [...x.turns, t].slice(-MAX_TURNS) } : x)) }));
  const ac = new AbortController();
  live.set(t.id, ac);
  const u = (f: (t: Turn) => Turn) => upd(cid, t.id, (x) => ({ ...f(x), last: Date.now() / 1000 }));
  ask(q, { page }, (e) => {
    if (e.type === "meta") u((x) => ({ ...x, model: e.model }));
    else if (e.type === "tool") u((x) => ({ ...x, tools: [...x.tools, e] }));
    else if (e.type === "evidence") u((x) => ({ ...x, evidence: [...x.evidence, e] }));
    else if (e.type === "token") u((x) => ({ ...x, text: x.text + e.text }));
    else if (e.type === "error") u((x) => ({ ...x, error: e.message, done: true }));
    else if (e.type === "done") u((x) => ({ ...x, done: true }));
  }, ac.signal, history, guide ? "/api/assistant/public" : "/api/assistant/ask")
    .then(() => u((x) => ({ ...x, done: true })))
    .catch((err) => u((x) => (x.stopped ? x : { ...x, error: err.name === "AbortError" ? undefined : err.message, done: true })))
    .finally(() => live.delete(t.id));
  return true;
}

export function retry(c: Convo, t: Turn, page: string) {
  set((s) => ({ ...s, active: c.id, convos: s.convos.map((x) => (x.id === c.id ? { ...x, turns: x.turns.filter((y) => y.id !== t.id) } : x)) }));
  send(t.q, page);
}
