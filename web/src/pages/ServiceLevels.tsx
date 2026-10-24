import { useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useApi } from "../api";
import TopBar from "../components/TopBar";
import { SleChart } from "../components/charts";
import { Empty, Load, Seg, ToneIcon } from "../components/ui";
import { pct100, sleTone, time } from "../format";

type Sle = {
  id: string; name: string; domain: string; goal_pct: number; value_pct: number; unit: string; n: number;
  series: { t: number; value: number }[]; classifiers: { id: string; name: string; share_pct: number }[]; incident_id?: string | null;
};
type Summary = { site: string; window_min: number; sites: string[]; sles: Sle[]; changes: { t: number; kind: string; text: string; ref?: string }[] };

const WINDOWS = [{ value: "60", label: "1 h" }, { value: "1440", label: "24 h" }, { value: "10080", label: "7 d" }];
const KIND = { fault: { c: "#D93A30", l: "Fault" }, action: { c: "#01A982", l: "Action" }, commit: { c: "#5B6B8C", l: "Commit" } } as Record<string, { c: string; l: string }>;

function Changes({ s }: { s: Summary }) {
  const now = Date.now() / 1000;
  const start = now - s.window_min * 60;
  return (
    <div className="card">
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <h2>Changes</h2>
        <span className="small weak">commits, injected faults and actions in the window</span>
      </div>
      <div style={{ position: "relative", height: 26, borderBottom: "1px solid var(--line)", margin: "4px 0 8px" }} aria-hidden>
        {s.changes.map((c, i) => (
          <span key={i} title={`${time(c.t)} ${c.text}`} style={{ position: "absolute", bottom: 6, left: `calc(${((c.t - start) / (now - start)) * 100}% - 6px)`, width: 11, height: 11, background: KIND[c.kind]?.c ?? "#6F7B76", transform: "rotate(45deg)", borderRadius: 2, boxShadow: "0 0 0 2px #fff" }} />
        ))}
      </div>
      {s.changes.length === 0 ? (
        <span className="small weak">No changes in this window.</span>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", gap: "6px 22px", flexWrap: "wrap", fontSize: 13 }}>
          {s.changes.map((c, i) => (
            <li key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span aria-hidden style={{ width: 9, height: 9, background: KIND[c.kind]?.c, transform: "rotate(45deg)", borderRadius: 1 }} />
              <span className="mono weak">{time(c.t)}</span>
              <span className="weak small">{KIND[c.kind]?.l}</span>
              {c.ref ? <Link to={`/actions/${c.ref}`}>{c.text}</Link> : <span>{c.text}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SleRow({ s }: { s: Sle }) {
  const nav = useNavigate();
  const tone = sleTone(s.value_pct, s.goal_pct);
  const open = (cls?: string) => s.incident_id && nav(`/root-cause/${s.incident_id}${cls ? `?classifier=${cls}` : ""}`);
  return (
    <div className="sle-row">
      <div>
        <div className="k">{s.name}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className={`big tone-${tone}`}>{pct100(s.value_pct)}</span>
          <ToneIcon tone={tone} size="18px" />
        </div>
        <div className="small weak">
          goal {s.goal_pct}% · n = {s.n} {s.unit}
        </div>
      </div>
      <SleChart series={s.series} goal={s.goal_pct} label={`${s.name} over the window; goal ${s.goal_pct}%`} />
      <div>
        <div className="k" style={{ marginBottom: 4 }}>Where failures fall</div>
        {s.classifiers.map((c) => (
          <div className="cls" key={c.id}>
            {s.incident_id && c.share_pct > 0 ? <button onClick={() => open(c.id)}>{c.name}</button> : <span>{c.name}</span>}
            <span className={`bar${c.share_pct >= 50 ? " crit" : ""}`} role="meter" aria-valuenow={c.share_pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${c.name} share`}>
              <i style={{ width: `${c.share_pct}%` }} />
            </span>
            <span className="small" style={{ textAlign: "right" }}>{c.share_pct}%</span>
          </div>
        ))}
        {tone !== "ok" && s.incident_id && (
          <Link to={`/root-cause/${s.incident_id}`} className="small">Open root cause →</Link>
        )}
      </div>
    </div>
  );
}

export default function ServiceLevels() {
  const [sp, setSp] = useSearchParams();
  const site = sp.get("site") ?? "campus";
  const win = sp.get("window") ?? "60";
  const domain = sp.get("domain") ?? "all";
  const q = useApi<Summary>(`/api/sle?site=${site}&window=${win}`, { url: `/api/stream/sle?site=${site}&window=${win}`, event: "sle" });
  const set = (k: string, v: string) => { sp.set(k, v); if (k === "site") sp.delete("domain"); setSp(sp, { replace: true }); };
  const domains = useMemo(() => Array.from(new Set(q.data?.sles.map((s) => s.domain) ?? [])), [q.data]);
  const sles = q.data?.sles.filter((s) => domain === "all" || s.domain === domain) ?? [];

  return (
    <>
      <TopBar title="Service levels">
        <Seg label="Site" value={site} onChange={(v) => set("site", v)} options={(q.data?.sites ?? ["campus", "fabric"]).map((s) => ({ value: s, label: s[0].toUpperCase() + s.slice(1) }))} />
        {domains.length > 1 && (
          <Seg label="Domain" value={domain} onChange={(v) => set("domain", v)} options={[{ value: "all", label: "All" }, ...domains.map((d) => ({ value: d, label: d === "wan" ? "WAN" : d[0].toUpperCase() + d.slice(1) }))]} />
        )}
        <Seg label="Time range" value={win} onChange={(v) => set("window", v)} options={WINDOWS} />
      </TopBar>
      <div className="content">
        <Load q={q} rows={6}>
          {(s) => (
            <>
              <Changes s={s} />
              <div className="card flush">
                <div className="card-h">
                  <h2>Success rate</h2>
                  <span className="small weak">share of {s.sles[0]?.unit ?? "minutes"} meeting the goal · updates live</span>
                </div>
                {sles.length === 0 ? <Empty title="No service levels for this site yet">The collector has not reported a full minute.</Empty> : sles.map((x) => <SleRow key={x.id + x.domain} s={x} />)}
              </div>
            </>
          )}
        </Load>
      </div>
    </>
  );
}
