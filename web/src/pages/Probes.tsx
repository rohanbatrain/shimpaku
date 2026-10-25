import { useSearchParams } from "react-router";
import { useApi } from "../api";
import TopBar from "../components/TopBar";
import { Load, Seg } from "../components/ui";
import { HeatGrid, HeatTable, Legend, METRICS, heatSummary, type Heat, type Metric } from "../components/ProbeHeatmap";

const WINDOWS = [{ value: "60", label: "1 h" }, { value: "1440", label: "24 h" }, { value: "10080", label: "7 d" }];

export default function Probes() {
  const [sp, setSp] = useSearchParams();
  const site = sp.get("site") ?? "campus";
  const win = sp.get("window") ?? "60";
  const metric = (sp.get("metric") ?? "loss_pct") as Metric;
  const set = (k: string, v: string) => { sp.set(k, v); setSp(sp, { replace: true }); };
  const q = useApi<Heat>(`/api/probes/heatmap?site=${site}&window=${win}`);
  return (
    <>
      <TopBar title="Probe heatmap" crumbs="Monitor · Service levels">
        <Seg label="Site" value={site} onChange={(v) => set("site", v)} options={[{ value: "campus", label: "Campus" }, { value: "fabric", label: "Fabric" }]} />
        <Seg label="Time range" value={win} onChange={(v) => set("window", v)} options={WINDOWS} />
        <Seg label="Colour by" value={metric} onChange={(v) => set("metric", v)} options={METRICS} />
      </TopBar>
      <div className="content">
        <Load q={q} rows={6}>
          {(h) => (
            <div className="card">
              <div className="hm-head">
                <h2>{h.site} · {h.paths.length} probe paths</h2>
                <span className="small weak">{h.source}</span>
              </div>
              {h.paths.length === 0 ? (
                <p className="small weak">No probe results stored for {site} in this window.</p>
              ) : (
                <>
                  <p className="small weak hm-sum" aria-live="polite">{heatSummary(h)}. Hover a cell for exact values and n.</p>
                  <HeatGrid h={h} metric={metric} />
                  <Legend metric={metric} h={h} />
                  <HeatTable h={h} />
                </>
              )}
            </div>
          )}
        </Load>
      </div>
    </>
  );
}
