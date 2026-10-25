// The (i) button: a plain-language explanation in a small popover. Opens on click or tap (and
// keyboard), closes on Escape, outside click or scroll. Rendered in a portal so a card's overflow
// never clips it.
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { TERMS, type Explainer, type Term } from "../glossary";
import "../info.css";

export default function Info({ k, e, label }: { k?: Term; e?: Explainer; label?: string }) {
  const x = e ?? (k ? TERMS[k] : null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number; up: boolean } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const id = useId();
  useLayoutEffect(() => {
    if (!open || !btn.current) return;
    const r = btn.current.getBoundingClientRect();
    const W = Math.min(320, window.innerWidth - 24);
    const left = Math.max(12, Math.min(window.innerWidth - W - 12, r.left + r.width / 2 - W / 2));
    const up = r.bottom + 200 > window.innerHeight && r.top > 220;
    setPos({ left, top: up ? r.top - 8 : r.bottom + 8, up });
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const close = (ev: Event) => {
      if (ev instanceof KeyboardEvent) { if (ev.key === "Escape") { setOpen(false); btn.current?.focus(); } return; }
      if (ev.type === "pointerdown" && (pop.current?.contains(ev.target as Node) || btn.current?.contains(ev.target as Node))) return;
      setOpen(false);
    };
    window.addEventListener("keydown", close);
    window.addEventListener("pointerdown", close);
    window.addEventListener("resize", close);
    document.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("keydown", close);
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("resize", close);
      document.removeEventListener("scroll", close, true);
    };
  }, [open]);
  if (!x) return null;
  return (
    <>
      <button ref={btn} type="button" className={`info-i${open ? " on" : ""}`} aria-label={label ?? `What is ${x.title.toLowerCase()}?`}
        aria-expanded={open} aria-controls={open ? id : undefined} onClick={(ev) => { ev.preventDefault(); ev.stopPropagation(); setOpen((o) => !o); }}>i</button>
      {open && pos && createPortal(
        <div ref={pop} id={id} role="dialog" aria-label={x.title} className={`info-pop${pos.up ? " up" : ""}`}
          style={{ left: pos.left, top: pos.top, width: Math.min(320, window.innerWidth - 24) }}>
          <b>{x.title}</b>
          <p>{x.body}</p>
        </div>,
        document.body,
      )}
    </>
  );
}
