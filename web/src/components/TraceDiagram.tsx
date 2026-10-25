// One Shim answer as a step diagram: question → each MCP tool call → evidence → answer.
import { Link } from "react-router";
import { idHref, toolHref, type Trace } from "../traces";
import { Busy, Rich } from "./ui";

export default function TraceDiagram({ t, compact = false }: { t: Trace; compact?: boolean }) {
  const total = t.steps.reduce((a, s) => a + (s.ms ?? 0), 0);
  return (
    <ol className={`trace${compact ? " compact" : ""}`} aria-label="Reasoning trace">
      <li className="trace-s q">
        <span className="trace-dot" aria-hidden>?</span>
        <div className="trace-c">
          <div className="trace-k">Question{t.model && <span className="weak"> · {t.model}</span>}</div>
          {!compact && <div>{t.q}</div>}
        </div>
      </li>
      {t.steps.map((s, i) => {
        const href = toolHref(s.name);
        return (
          <li key={i} className={`trace-s tool${s.ok === false ? " bad" : ""}`}>
            <span className="trace-dot" aria-hidden>{s.ok === false ? "✕" : i + 1}</span>
            <div className="trace-c">
              <div className="trace-tool">
                {href ? <Link to={href} className="mono">{s.name}</Link> : <span className="mono">{s.name}</span>}
                {s.args && <span className="mono weak trace-args" title={s.args}>({s.args})</span>}
                <span className="trace-meta">{s.ms != null ? `${s.ms} ms` : ""} {s.ok === false ? "· failed" : s.ok ? "· ok" : ""}</span>
              </div>
              {s.ids.length > 0 && (
                <div className="trace-ids">
                  {s.ids.map((id) => { const h = idHref(id); return h ? <Link key={id} to={h} className="chip">{id}</Link> : <span key={id} className="chip">{id}</span>; })}
                </div>
              )}
            </div>
          </li>
        );
      })}
      {!compact && t.evidence.length > 0 && (
        <li className="trace-s ev">
          <span className="trace-dot" aria-hidden>E</span>
          <div className="trace-c">
            <div className="trace-k">Evidence</div>
            <div className="trace-evs">
              {t.evidence.map((e, j) => e.href
                ? <Link key={j} to={e.href} className="trace-ev"><b>{e.title}</b><span className="weak small">{e.text} →</span></Link>
                : <div key={j} className="trace-ev"><b>{e.title}</b><span className="weak small">{e.text}</span></div>)}
            </div>
          </div>
        </li>
      )}
      <li className={`trace-s a${t.error ? " bad" : ""}`}>
        <span className="trace-dot" aria-hidden>{t.error ? "✕" : t.done ? "✓" : "…"}</span>
        <div className="trace-c">
          <div className="trace-k">Answer{t.steps.length > 0 && <span className="weak"> · {t.steps.length} tool call{t.steps.length === 1 ? "" : "s"}, {total} ms in tools</span>}</div>
          {!compact && (t.error ? <div className="tone-crit">{t.error}</div> : t.text ? <div className="trace-ans"><Rich text={t.text} /></div> : !t.done && <Busy />)}
        </div>
      </li>
    </ol>
  );
}
