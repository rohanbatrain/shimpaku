// REST + SSE client. Cookies ride same-origin; every non-GET echoes the CSRF cookie (double submit).
import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function csrf(): string {
  const m = document.cookie.match(/(?:^|; )(?:__Host-)?shimpaku_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

// GET responses kept for the session: a page opened again, or prefetched, draws at once from the
// last copy and refreshes in the background. Any write, sign-in or sign-out empties it.
const cache = new Map<string, unknown>();
const inflight = new Map<string, Promise<unknown>>();
export const cached = <T,>(path: string | null) => (path ? (cache.get(path) as T | undefined) : undefined);

/** Warm the cache for paths a page will ask for. Failures are ignored (a visitor's 403 stays quiet). */
export function prefetch(paths: string[]) {
  for (const p of paths) {
    if (cache.has(p) || inflight.has(p)) continue;
    const f = fetch(p, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then((r) => (r.ok ? r.json().then((d) => { cache.set(p, d); }) : undefined))
      .catch(() => undefined)
      .finally(() => inflight.delete(p));
    inflight.set(p, f);
  }
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  const method = (rest.method ?? (json !== undefined ? "POST" : "GET")).toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json", ...(rest.headers as Record<string, string>) };
  if (json !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") { headers["X-CSRF-Token"] = csrf(); cache.clear(); }
  const r = await fetch(path, { ...rest, method, headers, credentials: "same-origin", body: json !== undefined ? JSON.stringify(json) : rest.body });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail ?? msg;
    } catch {}
    if (r.status === 401) cache.clear();
    if (r.status === 401 && !location.pathname.startsWith("/login")) window.dispatchEvent(new Event("shimpaku:unauth"));
    throw new ApiError(r.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  const data = await r.json();
  if (method === "GET") cache.set(path, data);
  return data;
}

export type Loadable<T> = { data?: T; error?: ApiError | Error; loading: boolean; reload: () => void };

/** GET a resource; optionally keep it fresh from an SSE stream that pushes the same shape. */
export function useApi<T = any>(path: string | null, stream?: { url: string; event: string }): Loadable<T> {
  const [state, setState] = useState<{ data?: T; error?: Error; loading: boolean }>(() => ({ data: cached<T>(path), loading: !!path }));
  const [n, setN] = useState(0);
  const reload = useCallback(() => setN((x) => x + 1), []);
  useEffect(() => {
    if (!path) return;
    let live = true;
    // as before, the last data stays on screen while loading; a cached copy of this path is fresher
    setState((s) => ({ ...s, data: cached<T>(path) ?? s.data, loading: true, error: undefined }));
    api<T>(path)
      .then((data) => live && setState({ data, loading: false }))
      .catch((error) => live && setState({ error, loading: false }));
    return () => {
      live = false;
    };
  }, [path, n]);
  useSse<T>(stream?.url ?? null, stream?.event ?? "", (data) => setState({ data, loading: false }));
  return { ...state, reload };
}

export function useSse<T>(url: string | null, event: string, onData: (d: T) => void) {
  const cb = useRef(onData);
  cb.current = onData;
  useEffect(() => {
    if (!url) return;
    const es = new EventSource(url, { withCredentials: true });
    const h = (e: MessageEvent) => {
      try {
        cb.current(JSON.parse(e.data));
      } catch {}
    };
    es.addEventListener(event, h as EventListener);
    return () => es.close();
  }, [url, event]);
}

export type AskEvent =
  | { type: "meta"; model: string; demo?: boolean }
  | { type: "tool"; name: string; call: string; ms?: number; ok?: boolean; args?: string; ids?: string[] }
  | { type: "evidence"; title: string; text: string; href?: string }
  | { type: "token"; text: string }
  | { type: "error"; message: string }
  | { type: "done" };

/** POST /api/assistant/ask and parse the text/event-stream body incrementally. */
export async function ask(question: string, context: unknown, onEvent: (e: AskEvent) => void, signal?: AbortSignal,
  history?: { q: string; a: string }[], path = "/api/assistant/ask") {
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    signal,
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", "X-CSRF-Token": csrf() },
    body: JSON.stringify({ question, context, ...(history?.length ? { history } : {}) }),
  });
  if (!r.ok || !r.body) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail ?? msg;
    } catch {}
    throw new ApiError(r.status, msg);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = chunk
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).replace(/^ /, ""))
        .join("\n");
      if (data) {
        try {
          onEvent(JSON.parse(data));
        } catch {}
      }
    }
  }
}
