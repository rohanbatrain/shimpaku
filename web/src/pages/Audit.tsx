import Info from "../components/Info";
import { useState } from "react";
import { api, useApi } from "../api";
import TopBar from "../components/TopBar";
import { Busy, Load, Seg } from "../components/ui";
import { time } from "../format";
import "../explain.css";

type Block = {
  seq: number; ts: string; tool: string | null; kind: string | null; role: string | null; status: string | null;
  prev_hash: string; row_hash: string; recomputed: string; hash_ok: boolean; link_ok: boolean; ok?: boolean | null;
};
type Chain = { blocks: Block[]; total: number | null; next_before: number | null; verify: { ok?: boolean; rows?: number; first_bad_seq?: number; why?: string }; checked_at: number; demo?: boolean };
type Tamper = { edited_seq: number; field: string; before: string; after: string; rehash: boolean; first_bad_seq: number | null; why: string | null; blocks: Block[]; copy_only: boolean };

const short = (h?: string | null) => (h ? h.slice(0, 10) : "—");
const epoch = (iso: string) => Date.parse(iso) / 1000;

function BlockCard({ b, edited, broken, onTamper }: { b: Block; edited?: boolean; broken?: boolean; onTamper?: () => void }) {
  const ok = b.hash_ok && b.link_ok;
  return (
    <div className={`chain-b${ok ? "" : " bad"}${edited ? " edited" : ""}${broken ? " broken" : ""}`}>
      <div className="chain-seq mono">#{b.seq}</div>
      <div className="chain-main">
        <div className="chain-t">
          <b className="mono">{b.tool ?? b.kind ?? "—"}</b>
          {b.role && <span className="chip">{b.role}</span>}
          <span className={`chain-st${b.status && b.status !== "ok" ? " warn" : ""}`}>{b.status}</span>
        </div>
        <div className="chain-h mono small">
          <span title={b.prev_hash}>prev {short(b.prev_hash)}</span>
          <span aria-hidden>→</span>
          <span title={b.row_hash}>hash {short(b.row_hash)}</span>
          {!b.hash_ok && <span className="tone-crit" title={`recomputed ${b.recomputed}`}>recomputed {short(b.recomputed)} ≠ stored</span>}
          {b.hash_ok && !b.link_ok && <span className="tone-crit">prev ≠ hash of #{b.seq - 1}</span>}
        </div>
      </div>
      <div className="chain-r">
        <span className="small weak">{time(epoch(b.ts))}</span>
        <span className={ok ? "tone-ok" : "tone-crit"} aria-label={ok ? "hash and link verified" : "broken"}>{ok ? "✓" : "✗"}</span>
        {onTamper && <button className="btn chain-tbtn" onClick={onTamper} title="Simulate a forgery on an in-memory copy; the real log is never changed">Simulate tampering</button>}
      </div>
    </div>
  );
}

export default function Audit() {
  const [stack, setStack] = useState<(number | null)[]>([null]);
  const before = stack[stack.length - 1];
  const q = useApi<Chain>(`/api/explain/audit/chain?limit=15${before ? `&before=${before}` : ""}`);
  const [t, setT] = useState<{ seq: number; rehash: boolean; res?: Tamper; err?: string; busy?: boolean } | null>(null);

  const runTamper = (seq: number, rehash: boolean) => {
    setT({ seq, rehash, busy: true });
    window.setTimeout(() => document.querySelector(".ex-tamper")?.scrollIntoView({ block: "start", behavior: "smooth" }), 50);
    api<Tamper>(`/api/explain/audit/tamper?seq=${seq}&rehash=${rehash}`)
      .then((res) => setT({ seq, rehash, res }))
      .catch((e) => setT({ seq, rehash, err: e.message }));
  };

  return (
    <>
      <TopBar title="Audit chain" crumbs="Every MCP, REST and executor call, hash-chained: each row's hash covers the one before" />
      <div className="content">
        <Load q={q} rows={6}>
          {(c) => (
            <>
              <div className="card ex-verify">
                <div className={`ex-verify-v ${c.verify.ok ? "tone-ok" : "tone-crit"}`}>{c.verify.ok ? "✓ Chain verified" : c.verify.ok === false ? "✗ Chain broken" : "—"}</div>
                <div className="small weak">
                  {c.verify.ok ? <>every one of {c.verify.rows ?? c.total} rows re-hashed and linked{" "}</> :
                    c.verify.ok === false ? <>first bad row #{c.verify.first_bad_seq} · {c.verify.why}{" "}</> : null}
                  · checked {time(c.checked_at)}
                </div>
                <span style={{ flex: 1 }} />
                <button className="btn" onClick={q.reload}>Verify again</button>
              </div>
              {t && (
                <div className="card ex-tamper" aria-live="polite">
                  <div className="ex-head">
                    <h2 style={{ margin: 0 }}>Tamper test · row #{t.seq}<Info k="tamper" /></h2>
                    <span className="badge-sim">Simulation · the real log is never changed</span>
                    <Seg label="Forger" value={t.rehash ? "rehash" : "edit"} onChange={(v) => runTamper(t.seq, v === "rehash")}
                      options={[{ value: "edit", label: "Forger edits the row" }, { value: "rehash", label: "…and re-hashes it" }]} />
                    <Info k="rehash" />
                    <span style={{ flex: 1 }} />
                    <button className="btn" onClick={() => setT(null)}>Close</button>
                  </div>
                  <p className="small weak" style={{ margin: "6px 0 10px" }}>
                    The server copies rows #{Math.max(1, t.seq - 2)}–#{t.seq + 2} into memory, edits the copy and recomputes the hashes. The real log is never written.
                  </p>
                  {t.busy && <Busy />}
                  {t.err && <div className="notice crit">{t.err}</div>}
                  {t.res && (
                    <>
                      <div className={`ex-tamper-v ${t.res.first_bad_seq ? "crit" : "ok"}`}>
                        {t.res.first_bad_seq
                          ? <>Changed <span className="mono">{t.res.field}</span> “{t.res.before}” → “{t.res.after}”: the chain breaks at <b>#{t.res.first_bad_seq}</b> — {t.res.why}.</>
                          : <>No break found.</>}
                      </div>
                      <div className="chain">
                        {t.res.blocks.map((b) => <BlockCard key={b.seq} b={b} edited={b.seq === t.res!.edited_seq} broken={b.seq === t.res!.first_bad_seq} />)}
                      </div>
                    </>
                  )}
                </div>
              )}
              <div className="card">
                <div className="ex-head">
                  <h2 style={{ margin: 0 }}>Blocks<Info k="audit" /></h2>
                  <span className="small weak">newest first · {c.total ?? "—"} rows in total · role only, no identities or arguments</span>
                </div>
                <div className="chain">
                  {c.blocks.map((b) => <BlockCard key={b.seq} b={b} onTamper={() => runTamper(b.seq, false)} />)}
                </div>
                <div className="ex-pager">
                  <button className="btn" disabled={stack.length < 2} onClick={() => setStack((s) => s.slice(0, -1))}>← Newer</button>
                  <span className="small weak">{c.blocks.length ? `#${c.blocks[0].seq} – #${c.blocks[c.blocks.length - 1].seq}` : ""}</span>
                  <button className="btn" disabled={!c.next_before} onClick={() => setStack((s) => [...s, c.next_before])}>Older →</button>
                </div>
              </div>
            </>
          )}
        </Load>
      </div>
    </>
  );
}
