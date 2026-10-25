// Overview ticker: the newest Junos events as the collector stores them (SSE), linking to /events.
import { Link } from "react-router";
import { useEventStream } from "../live";
import { time } from "../format";
import "../live.css";

export default function EventTicker() {
  const s = useEventStream(6);
  return (
    <div className="card ticker" aria-label="Latest Junos events">
      <span className="ticker-h"><span className={`live-dot${s.available ? "" : " off"}`} aria-hidden />Junos events</span>
      {!s.available ? <span className="small weak" style={{ flex: 1 }}>Not available: {s.reason}</span> :
        s.rows.length === 0 ? <span className="small weak" style={{ flex: 1 }}>{s.loaded ? "No events stored yet." : "Reading events…"}</span> : (
          <ul aria-live="polite" aria-relevant="additions">
            {s.rows.slice(0, 5).map((e) => (
              <li key={e.id}>
                <span className="mono weak small">{time(e.t)}</span>
                <span className={`sev sev-${e.severity}`}>{e.severity}</span>
                <b>{e.device}</b>
                <span className="msg" title={`${e.type}: ${e.message}`}>{e.message || e.type}</span>
              </li>
            ))}
          </ul>
        )}
      <Link className="more" to="/events">All events →</Link>
    </div>
  );
}
