// Ask Shim as a floating chat window. Expand opens the same conversation full screen (/shim);
// closing the window never stops an answer (the conversation lives in shim.ts).
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { Close, Robot, Expand, History, Add } from "grommet-icons";
import { useSession } from "../session";
import { activeConvo, claim, newChat, send, useShim } from "../shim";
import { Composer, Conversation, ConvoList, VisitorNote, Welcome } from "./ShimChat";
import "../explain.css";

export default function AssistantPanel({ seed, onClose }: { seed?: string; onClose: () => void }) {
  const { who } = useSession();
  const visitor = who.role === "visitor";
  const s = useShim();
  const c = activeConvo(s);
  const loc = useLocation();
  const nav = useNavigate();
  const [hist, setHist] = useState(false);
  const seeded = useRef(false);
  useEffect(() => { claim(who.user); }, [who.user]);
  useEffect(() => {
    if (seed && !seeded.current && !visitor) {
      seeded.current = true; // refs survive StrictMode's effect replay, so the seed is sent once
      newChat();
      send(seed, loc.pathname);
    }
  }, []); // eslint-disable-line
  return (
    <aside className="sc-win" aria-label="Ask Shim assistant">
      <div className="sc-h">
        <Robot color="#00785C" size="18px" aria-hidden />
        <b className="sc-h-t" title={c?.title}>{c?.title ?? "Ask Shim"}</b>
        <span style={{ flex: 1 }} />
        {!visitor && <>
          <button className="icon-btn" onClick={() => setHist((h) => !h)} aria-pressed={hist} aria-label="Conversations" title="Conversations"><History size="15px" /></button>
          <button className="icon-btn" onClick={() => { newChat(); setHist(false); }} aria-label="New chat" title="New chat"><Add size="15px" /></button>
        </>}
        <button className="icon-btn" onClick={() => { onClose(); nav("/shim"); }} aria-label="Open full screen" title="Open full screen"><Expand size="14px" /></button>
        <button className="icon-btn" onClick={onClose} aria-label="Close (Esc). An answer in progress keeps going." title="Close (Esc)"><Close size="14px" /></button>
      </div>
      {hist && !visitor
        ? <div className="sc-hist"><ConvoList onPick={() => setHist(false)} /></div>
        : visitor ? <div className="sc-body"><VisitorNote /></div>
        : <Conversation convo={c} empty={<Welcome onPick={(q) => send(q, loc.pathname)} />} />}
      {!visitor && !hist && <Composer autoFocus />}
    </aside>
  );
}
