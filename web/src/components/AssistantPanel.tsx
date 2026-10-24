import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router";
import { Close, Send, Robot } from "grommet-icons";
import { ask, type AskEvent } from "../api";
import { Busy, Rich } from "./ui";
import { useSession } from "../session";

type Turn = {
  id: number;
  q: string;
  model?: string;
  tools: Extract<AskEvent, { type: "tool" }>[];
  evidence: Extract<AskEvent, { type: "evidence" }>[];
  text: string;
  done: boolean;
  error?: string;
};

const SUGGEST = ["What's the fabric looking like?", "Why did campus connect drop?", "What is waiting for my approval?"];

export default function AssistantPanel({ seed, onClose }: { seed?: string; onClose: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const seq = useRef(0);
  const seeded = useRef(false);
  const loc = useLocation();
  const busy = turns.length > 0 && !turns[turns.length - 1].done;
  // public viewers do not spend the LLM budget: Shim needs an operator or a live session
  const visitor = useSession()?.who.role === "visitor";

  const send = (question: string) => {
    if (!question.trim() || busy || visitor) return;
    setQ("");
    const id = ++seq.current;
    setTurns((t) => [...t, { id, q: question, tools: [], evidence: [], text: "", done: false }]);
    const upd = (f: (t: Turn) => Turn) => setTurns((all) => all.map((t) => (t.id === id ? f(t) : t)));
    abort.current = new AbortController();
    ask(question, { page: loc.pathname }, (e) => {
      if (e.type === "meta") upd((t) => ({ ...t, model: e.model }));
      else if (e.type === "tool") upd((t) => ({ ...t, tools: [...t.tools, e] }));
      else if (e.type === "evidence") upd((t) => ({ ...t, evidence: [...t.evidence, e] }));
      else if (e.type === "token") upd((t) => ({ ...t, text: t.text + e.text }));
      else if (e.type === "error") upd((t) => ({ ...t, error: e.message, done: true }));
      else if (e.type === "done") upd((t) => ({ ...t, done: true }));
    }, abort.current.signal)
      .then(() => upd((t) => ({ ...t, done: true })))
      .catch((err) => upd((t) => ({ ...t, error: err.message, done: true })));
  };

  useEffect(() => {
    input.current?.focus();
    if (seed && !seeded.current) {
      seeded.current = true; // refs survive StrictMode's effect replay, so the seed is sent once
      send(seed);
    }
  }, []);
  useEffect(() => {
    body.current?.scrollTo({ top: body.current.scrollHeight });
  }, [turns]);

  return (
    <aside className="panel" aria-label="Ask Shim assistant">
      <div className="panel-h">
        <Robot color="#00785C" size="18px" aria-hidden />
        <b>Ask Shim</b>
        <span className="small weak">answers cite tool calls</span>
        <span style={{ flex: 1 }} />
        <button className="icon-btn" onClick={onClose} aria-label="Close assistant (Esc)">
          <Close size="14px" />
        </button>
      </div>
      <div className="panel-b" ref={body} aria-live="polite">
        {visitor && (
          <div className="msg-a">
            <b>Request a live session to use Shim</b>
            <span className="weak">You are viewing read-only. During a live session Shim answers with the lab's own tools and can propose the fix for you to approve.</span>
            <a className="btn primary" href="/request" style={{ alignSelf: "flex-start" }}>Request a live session</a>
          </div>
        )}
        {!visitor && turns.length === 0 && (
          <div className="msg-a">
            <span className="weak">Shim reads the same service layer as this dashboard and can propose, never approve, a change.</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {SUGGEST.map((s) => (
                <button key={s} className="btn" style={{ justifyContent: "flex-start" }} onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t) => (
          <div key={t.id} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="msg-u">{t.q}</div>
            <div className="msg-a">
              {t.tools.length > 0 && (
                <div aria-label="Tool trace">
                  <div className="k" style={{ marginBottom: 4 }}>
                    Tool trace {t.model && <span className="weak" style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>· {t.model}</span>}
                  </div>
                  {t.tools.map((c, j) => (
                    <span key={j} className="chip" title={c.ms ? `${c.ms} ms` : undefined}>
                      <span style={{ color: c.ok === false ? "#B3261E" : "#00785C" }}>{c.ok === false ? "✕" : "✓"}</span>
                      {c.call}
                    </span>
                  ))}
                </div>
              )}
              {t.text ? <p style={{ margin: 0 }}><Rich text={t.text} /></p> : !t.done && <Busy />}
              {t.evidence.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {t.evidence.map((e, j) =>
                    e.href ? (
                      <Link key={j} to={e.href} className="ev">
                        <b>Evidence {j + 1} · {e.title}</b>
                        <div className="weak small">{e.text} →</div>
                      </Link>
                    ) : (
                      <div key={j} className="ev">
                        <b>Evidence {j + 1} · {e.title}</b>
                        <div className="weak small">{e.text}</div>
                      </div>
                    ),
                  )}
                </div>
              )}
              {t.error && <div className="notice crit">Shim could not answer: {t.error}</div>}
            </div>
          </div>
        ))}
      </div>
      {!visitor && <div className="panel-f">
        <form onSubmit={(e) => { e.preventDefault(); send(q); }}>
          <label htmlFor="ask" className="sr-only">Ask Shim</label>
          <input id="ask" ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about the lab…" autoComplete="off" />
          <button className="btn primary" type="submit" disabled={busy || !q.trim()} aria-label="Send">
            <Send size="14px" color="#04261B" />
          </button>
        </form>
      </div>}
    </aside>
  );
}
