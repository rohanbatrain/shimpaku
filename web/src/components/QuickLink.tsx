import { useEffect, useRef, useState } from "react";
import { Link as LinkIcon } from "grommet-icons";
import { api } from "../api";
import { Busy } from "./ui";

type Invite = { id: string; token: string; expires: number; link?: string | null };
// The link follows the https domain the operator is on (every app host serves the same lab). Over the ssh
// tunnel or plain http that would be http://127.0.0.1:8780/... (useless to them, token in clear): use the
// server's own link (SHIMPAKU_PUBLIC_URL) then.
const linkFor = (r: Invite) =>
  location.protocol === "https:" || !r.link ? `${location.origin}/start?i=${encodeURIComponent(r.token)}` : r.link;
const TTL = [{ h: 1, label: "1 hour" }, { h: 24, label: "24 hours" }, { h: 72, label: "3 days" }];
const at = (t: number) => new Date(t * 1000).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });

/** Operator only: make a single-use invite and hand over its link in one step, for someone who
 *  should land in a live session with one click (the invite page asks only "Agree and start"). */
export default function QuickLink() {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [ttl, setTtl] = useState(24);
  const [inv, setInv] = useState<Invite | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const link = inv ? linkFor(inv) : "";

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); } catch { setCopied(false); }
  };
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null); setCopied(false);
    try {
      const r = await api<Invite>("/api/admin/invites", { json: { note: note.trim(), ttl_hours: ttl } });
      setInv(r);
      await copy(linkFor(r));
    } catch (e: any) {
      setErr(e.status === 401 || e.status === 403 ? "Your operator session has ended. Sign in again." : e.message);
    } finally { setBusy(false); }
  };
  const reset = () => { setInv(null); setNote(""); setCopied(false); setErr(null); };

  return (
    <div className="quicklink" ref={box}>
      <button type="button" className="btn present-toggle" aria-expanded={open} onClick={() => { setOpen(!open); if (!open) reset(); }}
        title="A one-click link that drops someone into a live session">
        <LinkIcon size="13px" color="#00785C" aria-hidden /> Quick link
      </button>
      {open && (
        <div className="quicklink-panel" role="dialog" aria-label="Quick link">
          {!inv ? (
            <form onSubmit={create}>
              <b>Invite someone straight in</b>
              <p className="small weak" style={{ margin: "4px 0 10px" }}>One click on the link starts a live session for them (single use). If the lab is busy they queue.</p>
              <div className="field">
                <label htmlFor="ql-note">Who is it for? (optional, only you see it)</label>
                <input id="ql-note" value={note} maxLength={120} onChange={(e) => setNote(e.target.value)} placeholder="e.g. recruiter, company" autoComplete="off" />
              </div>
              <div className="field">
                <label htmlFor="ql-ttl">Link works for</label>
                <select id="ql-ttl" value={ttl} onChange={(e) => setTtl(Number(e.target.value))}>
                  {TTL.map((t) => <option key={t.h} value={t.h}>{t.label}</option>)}
                </select>
              </div>
              {err && <div className="notice crit" role="alert" style={{ marginBottom: 10 }}>{err}</div>}
              <button className="btn primary" type="submit" disabled={busy} style={{ width: "100%", justifyContent: "center" }}>
                {busy && <Busy />} Create and copy link
              </button>
            </form>
          ) : (
            <div>
              <b>{copied ? "Link copied" : "Link ready"}</b>
              <input className="quicklink-url" readOnly value={link} onFocus={(e) => e.currentTarget.select()} aria-label="Invite link" />
              <p className="small weak" style={{ margin: "6px 0 10px" }}>Works once, until {at(inv.expires)}. Revoke it with <code>shimpaku admin invite revoke {inv.id}</code>.</p>
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="btn primary" onClick={() => copy(link)}>{copied ? "Copied" : "Copy"}</button>
                {typeof navigator.share === "function" && (
                  <button type="button" className="btn" onClick={() => navigator.share({ title: "Shimpaku live lab", text: "A live session on my network lab:", url: link }).catch(() => {})}>Share</button>
                )}
                <button type="button" className="btn" onClick={reset}>Another</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
