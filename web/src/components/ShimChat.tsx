// Shim's chat, shared by the floating window and the full-screen page: the conversation, its
// composer and the conversation list. State lives in shim.ts, so a window can close mid-answer.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { Send, StopFill } from "grommet-icons";
import { Busy } from "./ui";
import TraceDiagram from "./TraceDiagram";
import { traceOf } from "../traces";
import { activeConvo, busyIn, deleteChat, newChat, openChat, retry, send, stop, useShim, type Convo, type Turn } from "../shim";
import { useSession } from "../session";
import { time } from "../format";
import "../shim.css";

/* ---- a small, safe Markdown subset: paragraphs, bullet / numbered lists, headings, **bold**, `code` ---- */
function inline(s: string, k: string): ReactNode[] {
  return s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((p, i) =>
    p.startsWith("**") && p.endsWith("**") && p.length > 4 ? <b key={k + i}>{p.slice(2, -2)}</b>
      : p.startsWith("`") && p.endsWith("`") && p.length > 2 ? <code key={k + i}>{p.slice(1, -1)}</code> : <span key={k + i}>{p}</span>);
}
export function Md({ text }: { text: string }) {
  const out: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const L = list.ordered ? "ol" : "ul";
    out.push(<L key={out.length}>{list.items.map((it, i) => <li key={i}>{inline(it, `${out.length}-${i}-`)}</li>)}</L>);
    list = null;
  };
  let para: string[] = [];
  const flushP = () => { if (para.length) out.push(<p key={out.length}>{inline(para.join(" "), `${out.length}-`)}</p>); para = []; };
  for (const raw of text.replace(/\r/g, "").split("\n")) {
    const line = raw.trim();
    const b = line.match(/^[-*•]\s+(.*)$/), n = line.match(/^\d+[.)]\s+(.*)$/), h = line.match(/^#{1,4}\s+(.*)$/);
    if (!line) { flushP(); flush(); continue; }
    if (b || n) {
      flushP();
      const ordered = !!n;
      if (list && list.ordered !== ordered) flush();
      list = list ?? { ordered, items: [] };
      list.items.push((b ?? n)![1]);
      continue;
    }
    flush();
    if (h) { flushP(); out.push(<h4 key={out.length}>{inline(h[1], `${out.length}-`)}</h4>); continue; }
    // "Issue: …" style section labels read as small headings
    const sec = line.match(/^(Issue|Impact|Cause|Root cause|Recommendation|Recommended action|Evidence|Summary|Next step)s?:\s*(.*)$/i);
    if (sec) { flushP(); out.push(<p key={out.length}><b>{sec[1]}:</b> {inline(sec[2], `${out.length}-`)}</p>); continue; }
    para.push(line);
  }
  flushP(); flush();
  return <div className="md">{out}</div>;
}

const SUGGEST: [RegExp, string[]][] = [
  [/^\/topology|^\/devices/, ["Is any link degraded right now?", "Why is leaf1 amber?", "Show OSPF and BFD health for the fabric"]],
  [/^\/sle/, ["Why did campus device health dip in the last hour?", "Which site is worse today, and why?"]],
  [/^\/root-cause|^\/incidents/, ["Summarise the latest incident in plain words", "What would fix the open incident?"]],
  [/^\/actions|^\/theatre/, ["What is waiting for my approval?", "Is VLAN 999 present on sw1 ge-0/0/5?"]],
  [/^\/hardware/, ["Which device is busiest right now?", "Are all seven VMs healthy?"]],
  [/^\/virga/, ["What fault is Virga injecting right now?", "Did Shimpaku detect the last drill?"]],
  [/^\/architecture|^\/guardrails|^\/audit|^\/benchmark/, ["How do the guardrails stop an unsafe change?", "Why is approval a dashboard session and not an MCP tool?", "How does the audit chain detect tampering?"]],
  [/.*/, ["What's the fabric looking like?", "Anything wrong in the lab right now?", "How does commit confirmed protect a change?", "How is Shimpaku built?"]],
];
const suggestFor = (p: string) => SUGGEST.find(([r]) => r.test(p))![1];

function useTick(on: boolean) {
  const [, set] = useState(0);
  useEffect(() => { if (!on) return; const i = window.setInterval(() => set((n) => n + 1), 1000); return () => window.clearInterval(i); }, [on]);
}

/** Always say what Shim is doing while an answer is open: the phase, the time so far, and a
 * plain note when nothing has arrived for a while (slow model) rather than leaving silence. */
function Progress({ t }: { t: Turn }) {
  useTick(true);
  const now = Date.now() / 1000;
  const el = Math.max(0, Math.round(now - t.at));
  const quiet = Math.round(now - (t.last ?? t.at));
  const last = t.tools[t.tools.length - 1];
  const phase = t.text ? "Writing the answer"
    : last ? <>Checking <span className="mono">{last.name.replace(/_/g, " ")}</span>{t.tools.length > 1 ? ` (${t.tools.length} tool calls so far)` : ""}</>
    : t.model ? <>Thinking with <span className="mono">{t.model}</span></>
    : "Connecting to Shim";
  return (
    <div className="sc-working" role="status" aria-live="polite">
      <Busy /> <span>{phase}…</span> <span className="sc-el mono">{el} s</span>
      {quiet >= 25 && <div className="sc-slow">Still working: no update for {quiet} s. The model can take a minute on a hard question; you can wait or press Stop.</div>}
    </div>
  );
}

function Answer({ c, t, page }: { c: Convo; t: Turn; page: string }) {
  const [steps, setSteps] = useState(false);
  const [copied, setCopied] = useState(false);
  const { who } = useSession();
  const ms = t.tools.reduce((s, x) => s + (x.ms ?? 0), 0);
  const acts = [...new Set(t.tools.flatMap((x) => x.ids ?? []).filter((i) => /^act-/.test(i)))];
  return (
    <div className="sc-a">
      <div className="sc-meta">
        <span className="sc-who">Shim</span>
        {t.model && <span className="sc-model">{t.model}</span>}
        {t.tools.length > 0 && (
          <button type="button" className="sc-steps-t" onClick={() => setSteps((s) => !s)} aria-expanded={steps}>
            {t.tools.length} tool call{t.tools.length === 1 ? "" : "s"}{ms ? ` · ${(ms / 1000).toFixed(1)} s` : ""} {steps ? "▴" : "▾"}
          </button>
        )}
      </div>
      {!t.done && <Progress t={t} />}
      {(steps || (!t.done && t.tools.length > 0)) && <div className="sc-trace"><TraceDiagram t={traceOf(t, c.id)} compact /></div>}
      {t.text && <Md text={t.text} />}
      {t.evidence.length > 0 && (
        <div className="sc-ev">
          {t.evidence.map((e, j) => e.href
            ? <Link key={j} to={e.href} className="ev"><b>Evidence {j + 1} · {e.title}</b><div className="weak small">{e.text} →</div></Link>
            : <div key={j} className="ev"><b>Evidence {j + 1} · {e.title}</b><div className="weak small">{e.text}</div></div>)}
        </div>
      )}
      {t.done && acts.length > 0 && (
        <div className="sc-acts">
          {acts.map((a) => (
            <Link key={a} className="btn primary" to={`/actions/${a}`}>{who.can_approve ? "Review and approve" : "Open the proposal"} · <span className="mono">{a}</span> →</Link>
          ))}
          <span className="small weak">Shim can propose; only a signed-in human approves.</span>
        </div>
      )}
      {t.error && <div className="notice crit">Shim could not answer: {t.error}</div>}
      {t.done && !t.error && !t.stopped && !t.text && (
        <div className="notice warn">Shim finished without writing an answer{t.tools.length ? ` (after ${t.tools.length} tool call${t.tools.length === 1 ? "" : "s"})` : ""}. Try again, or rephrase the question.</div>
      )}
      {t.stopped && !t.error && <div className="small weak">Stopped{t.text ? "" : " before an answer"}.</div>}
      {t.done && (
        <div className="sc-foot">
          {t.text && <button type="button" className="linkish" onClick={() => { void navigator.clipboard?.writeText(t.text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }); }}>{copied ? "Copied" : "Copy"}</button>}
          {(t.error || t.stopped || !t.text) && <button type="button" className="linkish" onClick={() => retry(c, t, page)}>Retry</button>}
          {t.tools.length > 0 && <Link to={`/assistant/trace?t=${t.id}`} className="linkish">Reasoning trace →</Link>}
        </div>
      )}
    </div>
  );
}

export function Conversation({ convo, empty }: { convo?: Convo; empty?: ReactNode }) {
  const loc = useLocation();
  const body = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const n = convo ? convo.turns.length + convo.turns.reduce((s, t) => s + t.text.length + t.tools.length, 0) : 0;
  useEffect(() => { if (stick.current) body.current?.scrollTo({ top: body.current.scrollHeight }); }, [n, convo?.id]);
  return (
    <div className="sc-body" ref={body} aria-live="polite"
      onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}>
      {!convo || convo.turns.length === 0 ? empty : convo.turns.map((t) => (
        <div key={t.id} className="sc-turn">
          <div className="msg-u">{t.q}</div>
          <Answer c={convo} t={t} page={loc.pathname} />
        </div>
      ))}
    </div>
  );
}

export function Welcome({ onPick }: { onPick: (q: string) => void }) {
  const loc = useLocation();
  return (
    <div className="sc-welcome">
      <b>Ask about the live lab, or how Shimpaku is built.</b>
      <span className="weak">Shim reads the same live data as this dashboard, and Shimpaku's own engineering documents for design and how-to questions. It shows every tool it used and names its sources. It can propose a fix; only a human approves it.</span>
      <div className="sc-sugg">
        {suggestFor(loc.pathname).map((s) => <button key={s} type="button" className="btn" onClick={() => onPick(s)}>{s}</button>)}
      </div>
    </div>
  );
}

export function VisitorNote() {
  return (
    <div className="sc-welcome">
      <b>Request a live session to use Shim</b>
      <span className="weak">You are viewing read-only. During a live session Shim answers with the lab's own tools and can propose the fix for you to approve.</span>
      <a className="btn primary" href="/request" style={{ alignSelf: "flex-start" }}>Request a live session</a>
    </div>
  );
}

export function Composer({ autoFocus }: { autoFocus?: boolean }) {
  const s = useShim();
  const c = activeConvo(s);
  const busy = busyIn(c);
  const [q, setQ] = useState("");
  const loc = useLocation();
  const ta = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (autoFocus) ta.current?.focus({ preventScroll: true }); }, [autoFocus, s.active]);
  useEffect(() => { const el = ta.current; if (el) { el.style.height = "auto"; el.style.height = `${Math.min(160, el.scrollHeight)}px`; } }, [q]);
  const go = () => { if (send(q, loc.pathname)) setQ(""); };
  return (
    <form className="sc-comp" onSubmit={(e) => { e.preventDefault(); go(); }}>
      <label htmlFor="shim-q" className="sr-only">Ask Shim</label>
      <textarea id="shim-q" ref={ta} rows={1} value={q} onChange={(e) => setQ(e.target.value)} placeholder={c?.turns.length ? "Ask a follow-up…" : "Ask about the lab…"}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); go(); } }} />
      {busy
        ? <button type="button" className="btn sc-stop" onClick={() => stop(c)} aria-label="Stop answering"><StopFill size="12px" color="currentColor" /> Stop</button>
        : <button className="btn primary" type="submit" disabled={!q.trim()} aria-label="Send"><Send size="14px" color="#04261B" /></button>}
    </form>
  );
}

export function ConvoList({ onPick }: { onPick?: () => void }) {
  const s = useShim();
  if (!s.convos.length) return <div className="small weak sc-none">No conversations yet.</div>;
  return (
    <ul className="sc-list" aria-label="Conversations">
      {s.convos.map((c) => (
        <li key={c.id} className={c.id === s.active ? "on" : undefined}>
          <button type="button" className="sc-li" onClick={() => { openChat(c.id); onPick?.(); }} aria-current={c.id === s.active}>
            <span className="sc-li-t">{c.title}</span>
            <span className="small weak">{time(c.at)} · {c.turns.length} question{c.turns.length === 1 ? "" : "s"}{busyIn(c) ? " · answering" : ""}</span>
          </button>
          <button type="button" className="sc-del" aria-label={`Delete “${c.title}”`} title="Delete" onClick={() => deleteChat(c.id)}>×</button>
        </li>
      ))}
    </ul>
  );
}

export { newChat };
