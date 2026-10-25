import { Link } from "react-router";
import { ago } from "../format";
import { clock, isLive, PHASE_LABEL, secs, useServerNow, useVirga } from "../virga";

/** Overview tile: the live drill, or the last one and its verdict. Renders nothing when the fault lab is off. */
export default function VirgaTile() {
  const { state } = useVirga();
  const t = state?.trial;
  const live = isLive(t);
  const now = useServerNow(live);
  if (!state) return null;
  const last = state.recent[0];
  if (!state.enabled && !last && !live) return null;
  return (
    <Link className={`card vg-tile${live ? " live" : ""}`} to="/virga">
      <div className="k">Virga · fault lab{state.simulated ? " · simulated" : ""}</div>
      {live && t ? (
        <div className="vg-tile-b">
          <b className="kpi-s tone-warn">Fault drill running</b>
          <span><span className="mono">{t.scenario}</span> {t.title} on {(t.nodes ?? []).join(", ")}</span>
          <span className="small weak">{PHASE_LABEL[t.phase]} · <span className="mono">{clock(now - t.t_start)}</span></span>
        </div>
      ) : last ? (
        <div className="vg-tile-b">
          <b className={`kpi-s ${last.verdict === "pass" ? "tone-ok" : last.verdict === "fail" ? "tone-crit" : ""}`}>
            Last drill: {last.verdict === "pass" ? "passed" : last.verdict === "fail" ? "failed" : "no verdict"}
          </b>
          <span><span className="mono">{last.scenario}</span> {last.title}</span>
          <span className="small weak">
            {ago(last.t_end ?? last.t_start)}{last.time_to_restore_s != null ? ` · restored in ${secs(last.time_to_restore_s)}` : ""}{last.incident_opened ? " · incident opened" : ""}
          </span>
        </div>
      ) : (
        <div className="vg-tile-b">
          <b className="kpi-s">No drill yet</b>
          <span className="small weak">The operator can inject a real fault and watch the lab detect it.</span>
        </div>
      )}
    </Link>
  );
}
