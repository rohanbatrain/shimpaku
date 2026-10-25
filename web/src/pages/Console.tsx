import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { api, ApiError, useApi } from "../api";
import TopBar from "../components/TopBar";
import { useSession } from "../session";
import "../console.css";

// Junos CLI console (presenter / operator only). The server checks every command against an
// operational-mode allow-list (shimpaku/console_policy.py); this page never decides what may run.

type Devs = { devices: string[]; quick: string[]; max_output: number; timeout_s: number };
type Entry = { id: number; device: string; command: string; output: string; ok: boolean; ms?: number; truncated?: boolean };

const HKEY = "shimpaku.console.history";

function loadHistory(): string[] {
  try { return JSON.parse(sessionStorage.getItem(HKEY) || "[]").slice(-100); } catch { return []; }
}

export default function Console() {
  const { who } = useSession();
  const devs = useApi<Devs>(who.role === "operator" ? "/api/console/devices" : null);
  const [device, setDevice] = useState("");
  const [cmd, setCmd] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<Entry[]>([]);
  const [hist, setHist] = useState<string[]>(loadHistory);
  const [hi, setHi] = useState<number | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const screen = useRef<HTMLDivElement>(null);
  const n = useRef(0);

  useEffect(() => {
    if (!device && devs.data?.devices.length) setDevice(devs.data.devices.includes("sw1") ? "sw1" : devs.data.devices[0]);
  }, [devs.data, device]);
  useEffect(() => { screen.current?.scrollTo({ top: screen.current.scrollHeight }); }, [log, busy]);

  if (who.role !== "operator") {
    return (
      <>
        <TopBar title="Console" />
        <div className="content"><p className="weak">The Junos console is available only in the operator's presenter session.</p></div>
      </>
    );
  }

  const run = async (text: string) => {
    const c = text.trim();
    if (!c || busy || !device) return;
    setBusy(true);
    setCmd("");
    setHi(null);
    const h = [...hist.filter((x) => x !== c), c].slice(-100);
    setHist(h);
    try { sessionStorage.setItem(HKEY, JSON.stringify(h)); } catch {}
    const id = ++n.current;
    try {
      const r = await api<{ command: string; output: string; duration_ms: number; truncated: boolean }>("/api/console/exec", { json: { device, command: c } });
      setLog((l) => [...l, { id, device, command: r.command, output: r.output, ok: true, ms: r.duration_ms, truncated: r.truncated }]);
    } catch (e) {
      const msg = e instanceof ApiError ? (e.status === 429 ? `${e.message}` : e.message) : String(e);
      setLog((l) => [...l, { id, device, command: c, output: msg, ok: false }]);
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") { e.preventDefault(); void run(cmd); }
    else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!hist.length) return;
      const i = hi == null ? hist.length - 1 : Math.max(0, hi - 1);
      setHi(i); setCmd(hist[i]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (hi == null) return;
      const i = hi + 1;
      if (i >= hist.length) { setHi(null); setCmd(""); } else { setHi(i); setCmd(hist[i]); }
    } else if (e.key.toLowerCase() === "l" && e.ctrlKey) { e.preventDefault(); setLog([]); }
  };

  const copy = async (en: Entry) => {
    try { await navigator.clipboard.writeText(en.output); setCopied(en.id); setTimeout(() => setCopied(null), 1500); } catch {}
  };

  return (
    <>
      <TopBar title="Console" crumbs={<span>Junos CLI · operational mode, read-only</span>} />
      <div className="content console-page">
        <div className="con-bar">
          <label className="con-dev">
            <span>Device</span>
            <select value={device} onChange={(e) => setDevice(e.target.value)} disabled={busy} aria-label="Device">
              {(devs.data?.devices ?? []).map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
          <div className="con-chips" role="group" aria-label="Quick commands">
            {(devs.data?.quick ?? []).map((q) => (
              <button key={q} type="button" className="con-chip" disabled={busy || !device} onClick={() => void run(q)}>{q}</button>
            ))}
          </div>
          <button type="button" className="btn con-clear" onClick={() => setLog([])} title="Clear (Ctrl+L)">Clear</button>
        </div>
        <div className="con-term" onClick={(e) => { if (!(e.target as HTMLElement).closest("button") && !window.getSelection()?.toString()) input.current?.focus(); }}>
          <div className="con-screen" ref={screen} aria-live="polite">
            {log.length === 0 && !busy && (
              <div className="con-hint">
                Operational mode only: <b>show</b>, <b>ping</b> (count ≤ 10), <b>traceroute</b>; filters | match, except, count, last, no-more.
                Changes go through Actions with approval. ↑/↓ history · Ctrl+L clear.
              </div>
            )}
            {log.map((en) => (
              <div key={en.id} className="con-entry">
                <div className="con-cmd">
                  <span className="con-prompt">admin@{en.device}&gt;</span> {en.command}
                  {en.ok && <span className="con-meta">{en.ms} ms{en.truncated ? " · output cut" : ""}</span>}
                  {en.ok && en.output && (
                    <button type="button" className="con-copy" onClick={() => void copy(en)} aria-label={`Copy output of ${en.command}`}>
                      {copied === en.id ? "Copied" : "Copy"}
                    </button>
                  )}
                </div>
                <pre className={en.ok ? "con-out" : "con-out con-err"}>{en.output.trim() ? en.output : (en.ok ? "(no output)" : "")}</pre>
              </div>
            ))}
            {busy && <div className="con-busy">running…</div>}
          </div>
          <div className="con-input">
            <span className="con-prompt">admin@{device || "…"}&gt;</span>
            <input ref={input} value={cmd} onChange={(e) => { setCmd(e.target.value); setHi(null); }} onKeyDown={onKey}
              autoFocus spellCheck={false} autoCapitalize="off" autoComplete="off" autoCorrect="off" maxLength={256}
              enterKeyHint="send" aria-label="Command" placeholder="show interfaces terse" disabled={!device} />
            <button type="button" className="btn primary con-run" disabled={busy || !cmd.trim() || !device} onClick={() => void run(cmd)}>Run</button>
          </div>
        </div>
        {devs.error && <p className="con-err-line">{devs.error.message}</p>}
      </div>
    </>
  );
}
