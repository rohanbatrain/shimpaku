import { Link, useNavigate, useSearchParams } from "react-router";
import { useApi } from "../api";
import TopBar from "../components/TopBar";
import { FixturePanel } from "../components/Lease";
import { useSession } from "../session";
import { Empty, Load, Pill, StatusPill } from "../components/ui";
import { ago, statusLabel } from "../format";

type A = { id: string; title: string; kind: string; tier: string; device: string; site: string; status: string; mode: string; proposed_by: string; approved_by?: string | null; created_at: number };

export default function Actions() {
  const q = useApi<{ actions: A[] }>("/api/actions", { url: "/api/stream/actions", event: "actions" });
  const nav = useNavigate();
  // ?ids=a,b&band=… : the actions behind one band of the guardrail funnel (/guardrails)
  const [sp] = useSearchParams();
  const only = sp.get("ids") ? new Set(sp.get("ids")!.split(",")) : null;
  const { who } = useSession();
  const tester = who.role === "tester";
  const showFixture = !who.replay && (tester || (who.role === "operator" && who.kind === "session"));
  const counts = (q.data?.actions ?? []).reduce<Record<string, number>>((m, a) => ((m[statusLabel(a.status)] = (m[statusLabel(a.status)] ?? 0) + 1), m), {});
  return (
    <>
      <TopBar title="Actions" crumbs="Proposed and executed changes · every commit runs under commit confirmed" />
      <div className="content">
        {showFixture && <FixturePanel tester={tester} />}
        <div className="grid4">
          {["Open", "Running", "Automatic", "Verified"].map((l) => (
            <div className="card" key={l}>
              <div className="k">{l}</div>
              <div className="big">{counts[l] ?? 0}</div>
            </div>
          ))}
        </div>
        <div className="card flush">
          {only && (
            <div className="notice" style={{ margin: 12 }}>
              Showing {only.size} action{only.size === 1 ? "" : "s"} from the guardrail funnel{sp.get("band") ? <> · <b>{sp.get("band")}</b></> : null}.{" "}
              <Link to="/actions">Show all</Link> · <Link to="/guardrails">Back to the funnel</Link>
            </div>
          )}
          <Load q={only ? { ...q, data: q.data && { ...q.data, actions: q.data.actions.filter((a) => only.has(String(a.id))) } } : q} empty={(d) => (d.actions.length ? null : <Empty title="No actions">Nothing has been proposed.</Empty>)}>
            {(d) => (
              <table className="t cards">
                <thead><tr><th>#</th><th>Action</th><th>Tier</th><th>Device</th><th>Mode</th><th>Proposed by</th><th>Created</th><th>Status</th></tr></thead>
                <tbody>
                  {d.actions.map((a) => (
                    <tr key={a.id} className="click" onClick={() => nav(`/actions/${a.id}`)}>
                      <td className="c-id"><Link to={`/actions/${a.id}`} className="mono" onClick={(e) => e.stopPropagation()}>{a.id}</Link></td>
                      <td data-label="Action"><b>{a.title}</b></td>
                      <td data-label="Tier"><Pill tone={a.tier === "T2" ? "warn" : "neutral"}>{a.tier}</Pill></td>
                      <td data-label="Device" className="mono">{a.device ?? "—"}</td>
                      <td data-label="Mode" className="weak">{a.mode ?? "—"}</td>
                      <td data-label="Proposed by" className="mono small">{a.proposed_by ?? "—"}</td>
                      <td data-label="Created" className="small weak">{ago(a.created_at)}</td>
                      <td data-label="Status"><StatusPill status={a.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Load>
        </div>
      </div>
    </>
  );
}
