// Ask Shim as a floating chat window. Expand opens the same conversation full screen (/shim);
// closing the window never stops an answer (the conversation lives in shim.ts).
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { Close, Robot, Expand, History, Add, Split } from "grommet-icons";
import { useSession } from "../session";
import { activeConvo, claim, newChat, send, setGuide, useShim } from "../shim";
import { Composer, Conversation, ConvoList, GuideBanner, GuideWelcome, Welcome } from "./ShimChat";
import "../explain.css";

export default function AssistantPanel({ seed, onClose }: { seed?: string; onClose: () => void }) {
  const { who } = useSession();
  const visitor = who.role === "visitor";
  const s = useShim();
  const c = activeConvo(s);
  const loc = useLocation();
  const nav = useNavigate();
  const [hist, setHist] = useState(false);
  const [wide, setWide] = useState(() => { try { return localStorage.getItem("shimpaku.shim.wide") === "1"; } catch { return false; } });
  const toggleWide = () => setWide((w) => { try { localStorage.setItem("shimpaku.shim.wide", w ? "0" : "1"); } catch {} return !w; });
  const seeded = useRef(false);
  useEffect(() => { claim(who.user); setGuide(visitor); }, [who.user, visitor]);
  useEffect(() => {
    if (seed && !seeded.current) {
      setGuide(visitor);
      seeded.current = true; // refs survive StrictMode's effect replay, so the seed is sent once
      newChat();
      send(seed, loc.pathname);
    }
  }, []); // eslint-disable-line
  return (
    <aside className={`sc-win${wide ? " wide" : ""}`} aria-label="Ask Shim assistant">
      <div className="sc-h">
        <Robot color="#00785C" size="18px" aria-hidden />
        <b className="sc-h-t" title={c?.title}>{c?.title ?? "Ask Shim"}</b>
        <span style={{ flex: 1 }} />
        {<>
          <button className="icon-btn" onClick={() => setHist((h) => !h)} aria-pressed={hist} aria-label="Conversations" title="Conversations"><History size="15px" /></button>
          <button className="icon-btn" onClick={() => { newChat(); setHist(false); }} aria-label="New chat" title="New chat"><Add size="15px" /></button>
        </>}
        <button className="icon-btn sc-wide-btn" onClick={toggleWide} aria-pressed={wide} aria-label={wide ? "Narrower window" : "Wider window"} title={wide ? "Narrower" : "Wider (for tables and charts)"}><Split size="14px" /></button>
        <button className="icon-btn" onClick={() => { onClose(); nav("/shim"); }} aria-label="Open full screen" title="Open full screen"><Expand size="14px" /></button>
        <button className="icon-btn" onClick={onClose} aria-label="Close (Esc). An answer in progress keeps going." title="Close (Esc)"><Close size="14px" /></button>
      </div>
      {visitor && !hist && <GuideBanner />}
      {hist
        ? <div className="sc-hist"><ConvoList onPick={() => setHist(false)} /></div>
        : <Conversation convo={c} guide={visitor}
            empty={visitor ? <GuideWelcome onPick={(q) => send(q, loc.pathname)} /> : <Welcome onPick={(q) => send(q, loc.pathname)} />} />}
      {!hist && <Composer autoFocus />}
    </aside>
  );
}
