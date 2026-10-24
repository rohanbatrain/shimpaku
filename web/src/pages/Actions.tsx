import { Link, useNavigate } from "react-router";
import { useApi } from "../api";
import TopBar from "../components/TopBar";
import { Empty, Load, Pill, StatusPill } from "../components/ui";
import { ago, statusLabel } from "../format";

type A = { id: string; title: string; kind: string; tier: string; device: string; site: string; status: string; mode: string; proposed_by: string; approved_by?: string | null; created_at: number };

export default function Actions() {
  const q = useApi<{ actions: A[] }>("/api/actions", { url: "/api/stream/actions", event: "actions" });
  const nav = useNavigate();
  const counts = (q.data?.actions ?? []).reduce<Record<string, number>>((m, a) => ((m[statusLabel(a.status)] = (m[statusLabel(a.status)] ?? 0) + 1), m), {});
  return (
    <>
      <TopBar title="Actions" crumbs="Proposed and executed changes · every commit runs under commit confirmed" />
      <div className="content">
        <div className="grid4">
          {["Open", "Running", "Automatic", "Verified"].map((l) => (
            <div className="card" key={l}>
              <div className="k">{l}</div>
              <div className="big">{counts[l] ?? 0}</div>
            </div>
          ))}
        </div>
        <div className="card flush">
          <Load q={q} empty={(d) => (d.actions.length ? null : <Empty title="No actions">Nothing has been proposed.</Empty>)}>
            {(d) => (
              <table className="t">
                <thead><tr><th>#</th><th>Action</th><th>Tier</th><th>Device</th><th>Mode</th><th>Proposed by</th><th>Created</th><th>Status</th></tr></thead>
                <tbody>
                  {d.actions.map((a) => (
                    <tr key={a.id} className="click" onClick={() => nav(`/actions/${a.id}`)}>
                      <td><Link to={`/actions/${a.id}`} className="mono" onClick={(e) => e.stopPropagation()}>{a.id}</Link></td>
                      <td><b>{a.title}</b></td>
                      <td><Pill tone={a.tier === "T2" ? "warn" : "neutral"}>{a.tier}</Pill></td>
                      <td className="mono">{a.device}</td>
                      <td className="weak">{a.mode}</td>
                      <td className="mono small">{a.proposed_by}</td>
                      <td className="small weak">{ago(a.created_at)}</td>
                      <td><StatusPill status={a.status} /></td>
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
