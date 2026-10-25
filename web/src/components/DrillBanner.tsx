import { Link } from "react-router";
import { clock, isLive, PHASE_LABEL, useServerNow, useVirga } from "../virga";

/** Shown to everyone, on every page, while a Virga fault drill is live. */
export default function DrillBanner() {
  const { state } = useVirga();
  const t = state?.trial;
  const live = isLive(t);
  const now = useServerNow(live);
  if (!live || !t) return null;
  return (
    <div className="drill-banner" role="status">
      <span className="drill-dot" aria-hidden />
      <span className="drill-msg">
        <strong>{state?.simulated ? "Simulated fault drill" : "Fault drill running"}:</strong>{" "}
        <span className="mono">{t.scenario}</span> {t.title} on {(t.nodes ?? []).join(", ") || "the lab"} <span aria-hidden>— {clock(now - t.t_start)}</span>
        <span className="sr-only">, {PHASE_LABEL[t.phase]}</span>
      </span>
      <span className="drill-links">
        Watch: <Link to="/topology">Topology</Link> · <Link to="/theatre">Theatre</Link> · <Link to="/virga">Fault lab</Link>
      </span>
    </div>
  );
}
