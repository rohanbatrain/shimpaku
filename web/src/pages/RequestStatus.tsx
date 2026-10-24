import { useEffect, useState } from "react";
import { Copy } from "grommet-icons";
import { api, ApiError } from "../api";
import { Busy, Pill } from "../components/ui";
import { PublicFrame, when } from "./Request";

type St = {
  id: string; status: "pending" | "approved" | "declined" | "expired" | "used"; slots: number[];
  slot_start: number | null; slot_end: number | null; opens_at: number | null; decline_note: string | null;
  link: string | null; ics_url: string | null; retention_days: number;
};
const TONE = { pending: "info", approved: "ok", declined: "crit", expired: "neutral", used: "neutral" } as const;
const LABEL = { pending: "Waiting for the owner", approved: "Approved", declined: "Declined", expired: "Expired", used: "Used" };

/** Public: the status token in the URL is the only key to this page. */
export default function RequestStatus() {
  const qs = new URLSearchParams(location.search);
  const s = qs.get("s") ?? "";
  const fresh = qs.get("new") === "1";
  const [st, setSt] = useState<St | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    const load = () => api<St>(`/api/session-requests/status?s=${encodeURIComponent(s)}`).then(setSt)
      .catch((e: ApiError) => setErr(e.status === 404 ? "This status link is not valid." : e.message));
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [s]);
  const self = `${location.origin}/request/status?s=${encodeURIComponent(s)}`;

  return (
    <PublicFrame lead="Your live-session request." sub="Keep this page's link: it is the only way to see the decision and, once approved, your session link.">
      <div style={{ width: 460, maxWidth: "100%" }}>
        <h1 style={{ marginBottom: 10 }}>Request status</h1>
        {err && <div className="notice crit" role="alert">{err} <a href="/request">Send a new request</a>.</div>}
        {!st && !err && <p className="weak"><Busy /> Loading…</p>}
        {st && (
          <>
            {fresh && (
              <div className="notice ok" role="status" style={{ marginBottom: 14 }}>
                <b>Request sent.</b> Bookmark this page or copy its link; nothing is e-mailed to you automatically.
                <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  <button className="btn" onClick={() => navigator.clipboard?.writeText(self).then(() => setCopied(true))}><Copy size="14px" /> {copied ? "Copied" : "Copy status link"}</button>
                </div>
              </div>
            )}
            <dl className="status-kv">
              <dt>Status</dt><dd><Pill tone={TONE[st.status]}>{LABEL[st.status]}</Pill></dd>
              <dt>You asked for</dt><dd>{st.slots.map((t) => <div key={t}>{when(t)}</div>)}</dd>
              {st.slot_start && <><dt>Your slot</dt><dd><b>{when(st.slot_start)}</b> (30 minutes)</dd></>}
              {st.opens_at && st.status === "approved" && <><dt>Link opens</dt><dd>{when(st.opens_at)}</dd></>}
              {st.decline_note && <><dt>Note</dt><dd style={{ whiteSpace: "pre-wrap" }}>{st.decline_note}</dd></>}
            </dl>
            {st.status === "pending" && <p className="weak">The owner reviews requests by hand. This page refreshes every minute.</p>}
            {st.status === "approved" && (
              <div className="notice info">
                <b>Your session link</b>
                <p style={{ margin: "6px 0" }}>It works once, from 10 minutes before your slot until the slot ends.</p>
                {st.link ? <div className="mono small" style={{ overflowWrap: "anywhere", marginBottom: 8 }}>{st.link}</div>
                  : <p className="small">The server has no public address configured; ask the owner for the link.</p>}
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {st.link && <a className="btn primary" href={st.link}>Open session</a>}
                  {st.ics_url && <a className="btn" href={st.ics_url} download>Add to calendar (.ics)</a>}
                </div>
              </div>
            )}
            {st.status === "declined" && <p className="weak">You may <a href="/request">send another request</a> for other times.</p>}
            {st.status === "expired" && <p className="weak">The times passed without a session. <a href="/request">Send a new request</a>.</p>}
            {st.status === "used" && <p className="weak">Your session link has been used. Thanks for trying Shimpaku.</p>}
            <p className="small weak" style={{ marginTop: 16 }}>Your name, contact and message are deleted {st.retention_days} days after your slot or a decline.</p>
          </>
        )}
      </div>
    </PublicFrame>
  );
}
