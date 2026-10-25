// Blast radius on the map: the incident's root-cause and blast-radius entities mapped to devices
// and intended links (server-side, /api/explain/incidents/:id/blast-map); everything else dims.
import Info from "./Info";
import ZoomPan from "./ZoomPan";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router";
import { useApi } from "../api";
import TopoMap, { type Topo } from "./TopoMap";
import { Load } from "./ui";
import "../explain.css";

type BM = { incident_id: string; title: string; nodes: { name: string; why: string[] }[]; links: { id: string; why: string[] }[]; unmapped: string[]; entities: number; demo?: boolean };

export default function BlastMap({ id }: { id: string }) {
  const topo = useApi<Topo>("/api/viz/topology");
  const bm = useApi<BM>(`/api/explain/incidents/${encodeURIComponent(id)}/blast-map`);
  const [all, setAll] = useState(false);
  const loc = useLocation();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (loc.hash === "#blast-map" && topo.data && bm.data) ref.current?.scrollIntoView({ block: "start" });
  }, [loc.hash, topo.data, bm.data]);
  return (
    <div className="card" id="blast-map" ref={ref}>
      <div className="ex-head">
        <h2 style={{ margin: 0 }}>Blast radius on the map<Info k="blast" /></h2>
        <span className="small weak">devices and links named by the incident's root causes and blast radius; the rest dimmed</span>
        <span style={{ flex: 1 }} />
        {bm.data && (bm.data.nodes.length > 0 || bm.data.links.length > 0) && (
          <button className="btn" aria-pressed={all} onClick={() => setAll((v) => !v)}>{all ? "Highlight blast radius" : "Show whole lab"}</button>
        )}
      </div>
      <Load q={bm} rows={4}>
        {(b) => (
          <Load q={topo} rows={4}>
            {(d) => {
              const none = b.nodes.length === 0 && b.links.length === 0;
              const focus = all || none ? null : { nodes: b.nodes.map((n) => n.name), links: b.links.map((l) => l.id) };
              const lk = (lid: string) => d.links.find((l) => l.id === lid);
              return (
                <>
                  {none && <div className="notice">No root-cause or blast-radius entity of this incident names a lab device or link{b.unmapped.length ? ` (entities: ${b.unmapped.join(", ")})` : ""}.</div>}
                  <ZoomPan label="blast radius map"><TopoMap data={d} compact focus={focus} /></ZoomPan>
                  <div className="topo-legend bm-legend" aria-label="Blast radius legend">
                    <span><i className="bm-sw focus" aria-hidden />in the blast radius</span>
                    <span><i className="bm-sw dim" aria-hidden />not named by this incident</span>
                    <span className="weak">ring colour = live state</span>
                  </div>
                  {!none && (
                    <ul className="bm-why">
                      {b.nodes.map((n) => (
                        <li key={n.name}><Link to={`/devices/${n.name}`} className="mono">{n.name}</Link> <span className="weak small">from {n.why.join(", ")}</span></li>
                      ))}
                      {b.links.map((l) => {
                        const x = lk(l.id);
                        return <li key={l.id}><span className="mono">{x ? `${x.a} ${x.a_if ?? ""} ↔ ${x.b} ${x.b_if ?? ""}` : l.id}</span> <span className="weak small">link · from {l.why.join(", ")}</span></li>;
                      })}
                      {b.unmapped.length > 0 && <li className="weak small">Not on the map: {b.unmapped.join(", ")}</li>}
                    </ul>
                  )}
                </>
              );
            }}
          </Load>
        )}
      </Load>
    </div>
  );
}
