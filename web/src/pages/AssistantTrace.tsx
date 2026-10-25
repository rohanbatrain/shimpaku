import { useState } from "react";
import TopBar from "../components/TopBar";
import TraceDiagram from "../components/TraceDiagram";
import { Empty } from "../components/ui";
import { clearTraces, MAX_TRACES, useTraces } from "../traces";
import { time } from "../format";
import { useSession } from "../session";
import "../explain.css";

export default function AssistantTrace() {
  const traces = useTraces();
  const { openAssistant } = useSession();
  const [sel, setSel] = useState<number | null>(null);
  const list = [...traces].reverse();
  const cur = list.find((t) => t.id === sel) ?? list[0];
  return (
    <>
      <TopBar title="Shim's reasoning" crumbs={`Each answer as steps: question, tool calls, evidence, answer · last ${MAX_TRACES} kept in this tab's memory only`} />
      <div className="content">
        {list.length === 0 ? (
          <div className="card">
            <Empty title="No answers yet">
              Ask Shim something; each answer's tool calls appear here. Traces live in this tab only and vanish on reload.
              <div style={{ marginTop: 10 }}><button className="btn primary" onClick={() => openAssistant()}>Ask Shim</button></div>
            </Empty>
          </div>
        ) : (
          <div className="trace-grid">
            <div className="card flush trace-list" role="list" aria-label="Answers">
              {list.map((t) => (
                <button key={t.id} role="listitem" className={`trace-li${t.id === cur?.id ? " sel" : ""}`} onClick={() => setSel(t.id)} aria-current={t.id === cur?.id}>
                  <span className="trace-li-q">{t.q}</span>
                  <span className="small weak">{time(t.at)} · {t.steps.length} tool call{t.steps.length === 1 ? "" : "s"}{t.error ? " · failed" : !t.done ? " · answering" : ""}</span>
                </button>
              ))}
              <div className="trace-li-f"><button className="btn" onClick={clearTraces}>Clear</button></div>
            </div>
            <div className="card">{cur && <TraceDiagram t={cur} />}</div>
          </div>
        )}
      </div>
    </>
  );
}
