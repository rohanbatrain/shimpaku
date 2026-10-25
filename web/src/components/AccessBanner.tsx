import { useSession } from "../session";

/** A full-width notice for anyone who is not signed in: they are watching the real lab, read-only,
 * and these are the two ways to get more (operator sign-in, or a requested live session). */
export default function AccessBanner() {
  const { who } = useSession();
  const when = (t: number) => new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";

  if (who.replay) {
    return (
      <div className="access-banner replay" role="status">
        <strong>Recorded replay, read-only.</strong>
        <span>The lab is offline right now, so this is a snapshot recorded from it at {when(who.replay.taken_at)}. Nothing here can be changed.</span>
      </div>
    );
  }
  if (who.role !== "visitor") return null;
  return (
    <div className="access-banner" role="status">
      <strong>Read-only mode.</strong>
      <span>You are watching the live lab (seven real Junos devices), but you cannot change anything.</span>
      <span className="access-actions">
        <a className="btn primary" href="/login">Sign in as operator</a>
        <a className="btn" href="/request">Request a live session</a>
      </span>
    </div>
  );
}
