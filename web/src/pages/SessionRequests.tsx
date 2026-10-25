import { useState } from "react";
import { Copy } from "grommet-icons";
import { api, useApi } from "../api";
import TopBar from "../components/TopBar";
import { Busy, Empty, Load, Pill, Seg } from "../components/ui";
import { ago } from "../format";
import { fromLocalInput, toLocalInput, when } from "./Request";

type Req = {
  id: string; status: string; slots: number[]; created: number; decided: number | null; decided_by: string | null;
  slot_start: number | null; slot_end: number | null; opens_at: number | null; decline_note: string | null;
  name: string | null; contact: string | null; message: string | null; purged: number | null;
};
type List = { requests: Req[]; pending: number; link_warning: string | null };
type Approved = Partial<Req> & { link: string | null; ics_url: string; link_warning: string | null };
const TONE: Record<string, "info" | "ok" | "crit" | "neutral"> = { pending: "info", approved: "ok", declined: "crit", expired: "neutral", used: "neutral" };

function Row({ r: given, onChange }: { r: Req; onChange: () => void }) {
  const [over, setOver] = useState<Partial<Req> | null>(null); // the server's reply to our own decision
  const r = { ...given, ...over };
  const [pick, setPick] = useState<string>(r.slots.length ? String(r.slots[0]) : "custom");
  const [custom, setCustom] = useState(toLocalInput((r.slots[0] ?? Date.now() / 1000 + 7200)));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<Approved | null>(null);
  const [copied, setCopied] = useState(false);
  const start = pick === "custom" ? fromLocalInput(custom) : Number(pick);

  const go = async (verb: "approve" | "decline") => {
    setBusy(true);
    setErr(null);
    try {
      const body = verb === "approve" ? { slot_start: new Date(start * 1000).toISOString() } : { note };
      const out = await api<Approved>(`/api/admin/session-requests/${r.id}/${verb}`, { json: body });
      setOver(out);
      if (verb === "approve") setDone(out);
      onChange();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  const contact = r.contact ?? "";
  const isUrl = /linkedin\.com\//i.test(contact);

  return (
    <article className="req" aria-labelledby={`rq-${r.id}`}>
      <div>
        <h3 id={`rq-${r.id}`}>{r.purged ? <span className="weak">(purged)</span> : r.name}<Pill tone={TONE[r.status] ?? "neutral"}>{r.status}</Pill></h3>
        {!r.purged && <div className="contact small">{isUrl ? <a href={contact.startsWith("http") ? contact : `https://${contact}`} target="_blank" rel="noopener noreferrer nofollow">{contact}</a> : <span className="mono">{contact}</span>}</div>}
        {r.message && <p className="msg small">{r.message}</p>}
        <div className="small weak" style={{ marginTop: 6 }}>{r.id} · sent {ago(r.created)}{r.decided_by ? ` · ${r.status} by ${r.decided_by}` : ""}</div>
      </div>
      <div>
        {r.status === "pending" ? (
          <>
            <fieldset>
              <legend>Slot (30 min)</legend>
              {r.slots.map((t, i) => (
                <label className="choice" key={t}>
                  <input type="radio" name={`slot-${r.id}`} checked={pick === String(t)} onChange={() => setPick(String(t))} /> {when(t)} <span className="weak">· choice {i + 1}</span>
                </label>
              ))}
              <label className="choice">
                <input type="radio" name={`slot-${r.id}`} checked={pick === "custom"} onChange={() => setPick("custom")} /> Other
                <input type="datetime-local" aria-label="Other start time" value={custom} step={900} onChange={(e) => { setCustom(e.target.value); setPick("custom"); }} />
              </label>
            </fieldset>
            <div className="acts">
              <button className="btn primary" disabled={busy || !start} onClick={() => go("approve")}>{busy && <Busy />} Approve</button>
              <input aria-label="Decline note (shown to the requester)" placeholder="Decline note (optional, shown to them)" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
              <button className="btn danger" disabled={busy} onClick={() => go("decline")}>Decline</button>
            </div>
          </>
        ) : (
          <div className="small">
            {r.slot_start && <div>Slot <b>{when(r.slot_start)}</b>; link opens {r.opens_at ? when(r.opens_at) : "—"}</div>}
            {r.decline_note && <div className="weak" style={{ whiteSpace: "pre-wrap" }}>Note: {r.decline_note}</div>}
            <div className="acts" style={{ marginTop: 8 }}>
              {r.slot_start && <a className="btn" href={`/api/admin/session-requests/${r.id}.ics`} download>Download .ics</a>}
              {r.status === "approved" && <button className="btn danger" disabled={busy} onClick={() => confirm("Withdraw this approval? The link stops working.") && go("decline")}>Withdraw</button>}
            </div>
          </div>
        )}
        {done && (
          <div className="notice ok" role="status" style={{ marginTop: 10 }}>
            <b>Approved.</b> Send this link to {r.contact}; the requester also sees it on their status page.
            {done.link ? <div className="link" style={{ margin: "6px 0" }}>{done.link}</div> : <div>{done.link_warning}</div>}
            <div className="acts">
              {done.link && <button className="btn" onClick={() => navigator.clipboard?.writeText(done.link!).then(() => setCopied(true))}><Copy size="14px" /> {copied ? "Copied" : "Copy link"}</button>}
              <a className="btn" href={done.ics_url} download>Download .ics</a>
            </div>
          </div>
        )}
        {err && <div className="notice crit" role="alert" style={{ marginTop: 10 }}>{err}</div>}
      </div>
    </article>
  );
}

type Inv = { id: string; note: string | null; status: string; expires: number; created: number; created_by: string | null; purged: number | null };
const INV_TONE: Record<string, "info" | "ok"> = { open: "ok", early: "info" };

/** Invites not yet used (quick links and approved slots): the operator can withdraw one here without the CLI. */
function OpenInvites() {
  const q = useApi<{ invites: Inv[] }>("/api/admin/invites");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [gone, setGone] = useState<string | null>(null);
  const open = (q.data?.invites ?? []).filter((i) => i.status === "open" || i.status === "early");
  const revoke = async (i: Inv) => {
    if (!confirm(`Revoke this invite${i.note ? ` (${i.note})` : ""}? Its link stops working.`)) return;
    setBusy(i.id); setErr(null); setGone(null);
    try {
      await api(`/api/admin/invites/${encodeURIComponent(i.id)}`, { method: "DELETE" });
      setGone(i.id);
      q.reload();
    } catch (e: any) {
      setErr(e.status === 404 ? "That invite is no longer open." : e.message);
      q.reload();
    } finally { setBusy(null); }
  };
  return (
    <section aria-labelledby="open-inv-h" style={{ marginTop: 22 }}>
      <h2 id="open-inv-h" style={{ margin: "0 0 4px" }}>Open invites{q.data ? ` (${open.length})` : ""}</h2>
      <p className="small weak" style={{ margin: "0 0 10px" }}>Links nobody has used yet, including Quick links made from the top bar. Revoke one to withdraw it.</p>
      {gone && <div className="notice ok" role="status" style={{ marginBottom: 10 }}>Revoked {gone}.</div>}
      {err && <div className="notice crit" role="alert" style={{ marginBottom: 10 }}>{err}</div>}
      <div className="card flush">
        <Load q={q} empty={() => (open.length ? null : <Empty title="No open invites">Make one with Quick link in the top bar.</Empty>)}>
          {() => (
            <ul className="inv-list">
              {open.map((i) => (
                <li key={i.id}>
                  <div className="inv-main">
                    <b>{i.purged ? "(erased)" : i.note || "No note"}</b> <Pill tone={INV_TONE[i.status] ?? "info"}>{i.status}</Pill>
                    <div className="small weak"><span className="mono">{i.id}</span> · expires {when(i.expires)}</div>
                  </div>
                  <button type="button" className="btn danger" disabled={busy === i.id} aria-label={`Revoke invite ${i.id}${i.note ? `, ${i.note}` : ""}`} onClick={() => revoke(i)}>
                    {busy === i.id && <Busy />} Revoke
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Load>
      </div>
    </section>
  );
}

export default function SessionRequests() {
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const q = useApi<List>(`/api/admin/session-requests${filter === "pending" ? "?status=pending" : ""}`);
  const [kept, setKept] = useState<Req[] | null>(null);
  return (
    <>
      <TopBar title="Session requests" crumbs="Live-session requests from the public Request page" />
      <div className="content">
        {q.data?.link_warning && <div className="notice warn" style={{ marginBottom: 14 }}>{q.data.link_warning}</div>}
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 14, flexWrap: "wrap" }}>
          <Seg label="Show" value={filter} onChange={(v) => { setKept(null); setFilter(v); }} options={[{ value: "pending", label: `Pending${q.data ? ` (${q.data.pending})` : ""}` }, { value: "all", label: "All" }]} />
          <span className="small weak">Public form: <a href="/request" target="_blank" rel="noopener">{location.origin}/request</a></span>
        </div>
        <div className="card flush">
          <Load q={q} empty={(d) => (d.requests.length || kept?.length ? null : <Empty title={filter === "pending" ? "No pending requests" : "No requests yet"}>Share <span className="mono">/request</span>; requests land here.</Empty>)}>
            {(d) => (
              <div className="req-list">
                {/* keep a just-approved row on screen (with its link) after it leaves the pending filter */}
                {[...d.requests, ...(kept ?? []).filter((k) => !d.requests.some((r) => r.id === k.id))].map((r) => (
                  <Row key={r.id} r={r} onChange={() => { setKept(d.requests); q.reload(); window.dispatchEvent(new Event("shimpaku:requests")); }} />
                ))}
              </div>
            )}
          </Load>
        </div>
        <OpenInvites />
      </div>
    </>
  );
}
