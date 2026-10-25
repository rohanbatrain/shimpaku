// Live Junos event stream (gNMI /junos/events as the collector stores them): filters, pause, newest first.
import Info from "../components/Info";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import TopBar from "../components/TopBar";
import { api, useSse } from "../api";

import { dedupe, type EventsPage, type JEvent } from "../live";
import "../live.css";

const CAP = 500;   // rows kept in memory; older ones fall off the end
const KINDS = ["link", "commit", "ospf", "bgp", "bfd", "lldp", "chassis", "other"];
const SEVS = ["error", "warning", "info"];
const DEVICES = ["r1", "r2", "sw1", "spine1", "spine2", "leaf1", "leaf2"];

export function EventLine({ e, fresh }: { e: JEvent; fresh?: boolean }) {
  return (
    <li className={`ev-row${fresh ? " new" : ""}`}>
      <span className="t" title={e.t ? new Date(e.t * 1000).toISOString() : ""}>{e.t ? new Date(e.t * 1000).toLocaleTimeString([], { hour12: false }) : "—"}</span>
      <span><span className={`sev sev-${e.severity}`}>{e.severity}</span></span>
      <span className="k ev-kind">{e.kind}</span>
      <span className="dev">{e.device}</span>
      <span className="m">
        {e.interface && <span className="ifn">{e.interface}</span>}
        {e.message || <span className="weak">no message</span>}
        <div className="ty">{e.type}</div>
      </span>
    </li>
  );
}

function match(e: JEvent, f: { device: string; kind: string; severity: string; q: string }) {
  return (!f.device || e.device === f.device) && (!f.kind || e.kind === f.kind) && (!f.severity || e.severity === f.severity)
    && (!f.q || `${e.message} ${e.type}`.toLowerCase().includes(f.q.toLowerCase()));
}

export default function Events() {
  const [sp, setSp] = useSearchParams();
  const f = { device: sp.get("device") ?? "", kind: sp.get("kind") ?? "", severity: sp.get("severity") ?? "", q: sp.get("q") ?? "" };
  const [qText, setQText] = useState(f.q);
  const [rows, setRows] = useState<JEvent[]>([]);
  const [held, setHeld] = useState<JEvent[]>([]);
  const [paused, setPaused] = useState(false);
  const [after, setAfter] = useState<number | null>(null);
  const [meta, setMeta] = useState<Partial<EventsPage>>({});
  const [freshIds, setFreshIds] = useState<Set<number>>(new Set());
  const [older, setOlder] = useState<"idle" | "busy" | "end">("idle");
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const fRef = useRef(f);
  fRef.current = f;

  const setF = (k: string, v: string) => {
    const next = new URLSearchParams(sp);
    if (v) next.set(k, v); else next.delete(k);
    setSp(next, { replace: true });
  };
  useEffect(() => { const id = window.setTimeout(() => qText !== f.q && setF("q", qText), 350); return () => window.clearTimeout(id); });

  const qs = useMemo(() => new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString(), [f.device, f.kind, f.severity, f.q]);
  useEffect(() => {
    let live = true;
    setRows([]); setHeld([]); setOlder("idle");
    api<EventsPage>(`/api/live/events?limit=150${qs ? `&${qs}` : ""}`).then((p) => {
      if (!live) return;
      setMeta(p);
      setRows(p.events ?? []);
      setAfter((a) => a ?? p.last_id ?? 0);
      if ((p.events ?? []).length < 150) setOlder("end");
    }).catch((e) => live && setMeta({ available: false, reason: String(e.message ?? e) }));
    return () => { live = false; };
  }, [qs]);

  useSse<JEvent[]>(after == null ? null : `/api/live/stream/events?after_id=${after}`, "events", (batch) => {
    const fresh = batch.filter((e) => match(e, fRef.current)).sort((a, b) => b.id - a.id);
    if (!fresh.length) return;
    if (pausedRef.current) { setHeld((h) => [...fresh, ...h].slice(0, CAP)); return; }
    setRows((r) => dedupe([...fresh, ...r]).slice(0, CAP));
    setFreshIds(new Set(fresh.map((e) => e.id)));
  });

  const resume = () => {
    setRows((r) => dedupe([...held, ...r]).slice(0, CAP));
    setFreshIds(new Set(held.map((e) => e.id)));
    setHeld([]);
    setPaused(false);
  };
  const loadOlder = () => {
    const min = rows.length ? rows[rows.length - 1].id : null;
    if (min == null) return;
    setOlder("busy");
    api<EventsPage>(`/api/live/events?limit=150&before_id=${min}${qs ? `&${qs}` : ""}`).then((p) => {
      setRows((r) => dedupe([...r, ...(p.events ?? [])]).slice(0, CAP));
      setOlder((p.events ?? []).length < 150 || rows.length >= CAP ? "end" : "idle");
    }).catch(() => setOlder("idle"));
  };

  return (
    <>
      <TopBar title="Junos events" crumbs="gNMI /junos/events from all seven devices, live">
      </TopBar>
      <div className="content">
        <div className="card">
          <div className="ev-filters" role="search">
            <label>Device
              <select value={f.device} onChange={(e) => setF("device", e.target.value)}>
                <option value="">All</option>{DEVICES.map((d) => <option key={d}>{d}</option>)}
              </select>
            </label>
            <label>Kind
              <select value={f.kind} onChange={(e) => setF("kind", e.target.value)}>
                <option value="">All</option>{KINDS.map((d) => <option key={d}>{d}</option>)}
              </select>
            </label>
            <label>Severity
              <select value={f.severity} onChange={(e) => setF("severity", e.target.value)}>
                <option value="">All</option>{SEVS.map((d) => <option key={d}>{d}</option>)}
              </select>
            </label>
            <label className="grow">Text
              <input type="search" value={qText} placeholder="message or event id" onChange={(e) => setQText(e.target.value)} maxLength={80} />
            </label>
            <button className={`btn${paused ? " primary" : ""}`} onClick={() => (paused ? resume() : setPaused(true))} aria-pressed={paused}>
              {paused ? `Resume${held.length ? ` (${held.length} new)` : ""}` : "Pause"}
            </button>
          </div>
          <div className="small weak" style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span className={`live-dot${paused || meta.available === false ? " off" : ""}`} aria-hidden />
            {meta.available === false ? `Not available: ${meta.reason}` : paused ? "Paused — new events are held, not shown" : "Streaming — new events appear at the top"}
            <span>· newest first · up to {CAP} kept in this page</span>
            {meta.stored != null && <span>· {meta.stored} stored{meta.first_t ? ` since ${new Date(meta.first_t * 1000).toLocaleString([], { hour12: false })}` : ""}</span>}
          </div>
        </div>
        <div className="card flush">
          <div className="card-h"><h2>Events<Info k="junos_events" /></h2><span className="small weak">{rows.length} shown · the collector keeps link, commit, routing, BFD, LLDP and chassis events; session noise is dropped</span></div>
          {rows.length === 0 ? (
            <div style={{ padding: 16 }} className="small weak">{meta.available === false ? "The event table is not available." : "No events match."}</div>
          ) : (
            <ul className="ev-list" aria-live="off">
              {rows.map((e) => <EventLine key={e.id} e={e} fresh={freshIds.has(e.id)} />)}
            </ul>
          )}
          {rows.length > 0 && (
            <div style={{ padding: 12, borderTop: "1px solid var(--line)", textAlign: "center" }}>
              {older === "end" ? <span className="small weak">{rows.length >= CAP ? `Showing the newest ${CAP}; narrow the filters to look further back.` : "No older events."}</span> :
                <button className="btn" onClick={loadOlder} disabled={older === "busy"}>{older === "busy" ? "Loading…" : "Load older"}</button>}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
