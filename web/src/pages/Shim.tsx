// Shim, full screen: the conversation list beside the open conversation.
import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router";
import { Contract } from "grommet-icons";
import TopBar from "../components/TopBar";
import { Composer, Conversation, ConvoList, VisitorNote, Welcome } from "../components/ShimChat";
import { activeConvo, claim, clearAll, newChat, send, useShim } from "../shim";
import { useSession } from "../session";
import "../explain.css";

export default function Shim() {
  const { who, openAssistant } = useSession();
  const nav = useNavigate();
  // back to the page this was opened from (or Overview), with the same conversation in the bubble
  const collapse = () => {
    if ((window.history.state?.idx ?? 0) > 0) nav(-1); else nav("/");
    window.setTimeout(() => openAssistant(), 0);
  };
  const visitor = who.role === "visitor";
  const s = useShim();
  const loc = useLocation();
  useEffect(() => { claim(who.user); }, [who.user]);
  return (
    <>
      <TopBar title="Shim" crumbs="The built-in assistant · saved in this browser, wiped on sign-out">
        <button type="button" className="btn" onClick={collapse} title="Back to the page you were on, chat in the bubble">
          <Contract size="14px" color="currentColor" aria-hidden /> Back to bubble
        </button>
      </TopBar>
      <div className="sc-page">
        {visitor ? <div className="card sc-page-main"><div className="sc-body"><VisitorNote /></div></div> : <>
          <aside className="card sc-page-side">
            <button type="button" className="btn primary" onClick={newChat}>+ New chat</button>
            <ConvoList />
            {s.convos.length > 0 && <button type="button" className="linkish small" onClick={() => { if (confirm("Delete every saved conversation in this browser?")) clearAll(); }}>Delete all</button>}
          </aside>
          <section className="card sc-page-main" aria-label="Conversation">
            <Conversation convo={activeConvo(s)} empty={<Welcome onPick={(q) => send(q, loc.pathname)} />} />
            <Composer autoFocus />
          </section>
        </>}
      </div>
    </>
  );
}
