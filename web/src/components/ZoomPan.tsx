// Zoom and pan for any diagram. Buttons (+, −, reset), Ctrl/⌘ + wheel or a trackpad pinch to zoom
// at the pointer, drag to pan once zoomed in. A plain wheel still scrolls the page, and a click
// that was not a drag reaches the diagram (nodes stay clickable).
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import "../zoompan.css";

const MIN = 1, MAX = 4;

export default function ZoomPan({ children, label = "diagram", className }: { children: ReactNode; label?: string; className?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [z, setZ] = useState({ s: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; ox: number; oy: number; moved: boolean; id: number } | null>(null);
  const clamp = useCallback((s: number, x: number, y: number) => {
    const el = box.current;
    if (!el || s <= 1) return { s: 1, x: 0, y: 0 };
    const w = el.clientWidth, h = el.clientHeight;
    return { s, x: Math.min(0, Math.max(w - w * s, x)), y: Math.min(0, Math.max(h - h * s, y)) };
  }, []);
  const zoomAt = useCallback((f: number, cx?: number, cy?: number) => setZ((z) => {
    const el = box.current;
    const s = Math.min(MAX, Math.max(MIN, z.s * f));
    const px = cx ?? (el ? el.clientWidth / 2 : 0), py = cy ?? (el ? el.clientHeight / 2 : 0);
    // keep the point under the pointer fixed
    return clamp(s, px - ((px - z.x) * s) / z.s, py - ((py - z.y) * s) / z.s);
  }), [clamp]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return; // trackpad pinch arrives as ctrl + wheel
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.0025), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, [zoomAt]);
  const zoomed = z.s > 1.001;
  return (
    <div className={`zp${zoomed ? " zoomed" : ""}${className ? " " + className : ""}`} ref={box}
      onPointerDown={(e) => { if (zoomed && e.button === 0) drag.current = { x: e.clientX, y: e.clientY, ox: z.x, oy: z.y, moved: false, id: e.pointerId }; }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        const dx = e.clientX - d.x, dy = e.clientY - d.y;
        if (!d.moved && Math.hypot(dx, dy) > 4) { d.moved = true; box.current?.setPointerCapture(d.id); }
        if (d.moved) setZ((z) => clamp(z.s, d.ox + dx, d.oy + dy));
      }}
      onPointerUp={() => { setTimeout(() => { drag.current = null; }, 0); }}
      onClickCapture={(e) => { if (drag.current?.moved) { e.stopPropagation(); e.preventDefault(); } }}>
      <div className="zp-in" style={{ transform: `translate(${z.x}px, ${z.y}px) scale(${z.s})` }}>{children}</div>
      <div className="zp-ctl" role="group" aria-label={`Zoom the ${label}`}>
        <button type="button" onClick={() => zoomAt(1.4)} disabled={z.s >= MAX} aria-label="Zoom in" title="Zoom in (or Ctrl/⌘ + scroll, pinch)">+</button>
        <button type="button" onClick={() => zoomAt(1 / 1.4)} disabled={!zoomed} aria-label="Zoom out" title="Zoom out">−</button>
        <button type="button" onClick={() => setZ({ s: 1, x: 0, y: 0 })} disabled={!zoomed} aria-label="Reset zoom" title="Fit">⤢</button>
      </div>
      {zoomed && <span className="zp-hint" aria-hidden>drag to pan</span>}
    </div>
  );
}
