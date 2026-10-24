import { useState } from "react";
import { api } from "../api";
import { Busy } from "./ui";

/** The DPDP notice line on every page that collects personal data: erasure is self-service. */
export function DeletionLine({ days, contact }: { days: number; contact: string | null | undefined }) {
  return (
    <li>
      <b>Deletion:</b> Delete your data any time from your status page (or your session page) — it is erased immediately.
      Otherwise it is deleted automatically {days} days after your slot.
      {contact && <> Or write to <span className="mono">{contact}</span>.</>} You may also complain to India's Data Protection Board.
    </li>
  );
}

/** "Delete my data": a button, an explicit confirm step, then a done state. ``body`` is posted
 * as JSON (the capability token), or nothing for the tester's session route. */
export function EraseData({ path, body, what, onDone }: { path: string; body?: unknown; what: string; onDone?: () => void }) {
  const [step, setStep] = useState<"idle" | "confirm" | "busy" | "done">("idle");
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setStep("busy");
    setErr(null);
    try {
      await api(path, body === undefined ? { method: "POST" } : { json: body });
      setStep("done");
      onDone?.();
    } catch (e: any) {
      setErr(e.status === 429 ? "Too many attempts. Wait a minute and try again." : e.message);
      setStep("confirm");
    }
  };
  if (step === "done")
    return <div className="notice ok erase-box" role="status"><b>Your data has been deleted.</b> {what}: gone from this lab now; nothing else to do.</div>;
  return (
    <div className="erase-box">
      {step === "idle" ? (
        <button type="button" className="btn danger" onClick={() => setStep("confirm")}>Delete my data</button>
      ) : (
        <div className="notice warn" role="alertdialog" aria-labelledby="erase-q">
          <p id="erase-q" style={{ margin: "0 0 8px" }}><b>Delete your data now?</b> {what}: erased immediately; this cannot be undone. Any session you have is ended.</p>
          {err && <div className="notice crit" role="alert" style={{ marginBottom: 8 }}>{err}</div>}
          <div className="erase-actions">
            <button type="button" className="btn danger" onClick={go} disabled={step === "busy"}>{step === "busy" && <Busy />} Yes, delete it</button>
            <button type="button" className="btn" onClick={() => setStep("idle")} disabled={step === "busy"}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
