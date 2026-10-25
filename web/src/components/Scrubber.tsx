// Time-travel scrubber: a minute slider over stored history (≤ 24 h), deep-linked as ?t=<epoch s>.
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useRange } from "../live";
import { time } from "../format";
import "../live.css";

const dayTime = (t: number) => {
  const d = new Date(t * 1000);
  const today = new Date();
  return d.toDateString() === today.toDateString() ? time(t) : `${d.toLocaleDateString([], { weekday: "short" })} ${time(t)}`;
};

/** The ?t= search param as a number, or null for live; setter keeps other params. */
export function useTimeParam(): [number | null, (t: number | null) => void] {
  const [sp, setSp] = useSearchParams();
  const raw = sp.get("t");
  const t = raw && /^\d+(\.\d+)?$/.test(raw) ? Number(raw) : null;
  const set = (v: number | null) => {
    const next = new URLSearchParams(sp);
    if (v == null) next.delete("t"); else next.set("t", String(Math.floor(v)));
    setSp(next, { replace: true });
  };
  return [t, set];
}

export default function Scrubber({ t, onChange, label = "Time travel" }: { t: number | null; onChange: (t: number | null) => void; label?: string }) {
  const rq = useRange();
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now() / 1000), 30000); return () => window.clearInterval(id); }, []);
  const hi = Math.floor(now / 60) * 60;
  const from = rq.data?.from ?? null;
  const lo = from ? Math.ceil(from / 60) * 60 : hi - 3600;
  const [drag, setDrag] = useState<number | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const value = drag ?? (t == null ? hi : Math.max(lo, Math.min(hi, Math.floor(t / 60) * 60)));
  const commit = (v: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { onChange(v >= hi ? null : v); setDrag(null); }, 250);
  };
  const live = t == null && drag == null;
  const noHistory = rq.data && !from;
  return (
    <div className="scrub" role="group" aria-label={label}>
      <span className={`scrub-asof${live ? "" : " past"}`} aria-live="polite">{live ? "Live" : `As of ${dayTime(value)}`}</span>
      <input type="range" min={lo} max={hi} step={60} value={value} disabled={!!noHistory || lo >= hi}
        aria-label={`${label}: choose a minute`} aria-valuetext={live ? "live" : `as of ${dayTime(value)}`}
        onChange={(e) => { const v = Number(e.target.value); setDrag(v); commit(v); }} />
      <button className="btn" onClick={() => { window.clearTimeout(timer.current); setDrag(null); onChange(null); }} disabled={live} aria-label="Return to live">
        ● Live
      </button>
      <div className="scrub-ends" aria-hidden><span>{dayTime(lo)}</span><span>now</span></div>
      <div className="scrub-note">
        {noHistory ? "No stored samples yet — nothing to travel back to." :
          rq.data ? `Redrawn from stored minutes and 10 s samples. History kept since ${dayTime(rq.data.samples.first ?? lo)}${(rq.data.samples.first ?? lo) < lo ? "; the slider covers the last 24 h" : ""}.` : "Reading stored history…"}
      </div>
    </div>
  );
}
