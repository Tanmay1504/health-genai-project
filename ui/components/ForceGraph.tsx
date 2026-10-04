"use client";

// ForceGraph — renders a fraud-ring subgraph as SVG using d3-force.
//
// WHY compute the layout up front instead of animating?
//   We run the physics simulation for a fixed number of ticks inside useMemo,
//   then render static SVG. No per-frame React re-renders, the result is
//   deterministic for the same data, and the code is short enough to read in
//   one sitting. d3-force does the maths; React draws the SVG.
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, SimulationLinkDatum, SimulationNodeDatum } from "d3-force";
import { useMemo, useState } from "react";
import type { GraphLink, GraphNode } from "@/lib/api";

export const KIND_COLOR: Record<GraphNode["kind"], string> = {
  Patient: "#4f8cff",
  Organization: "#e5534b",
  Practitioner: "#c69026",
  Address: "#8b949e",
  Phone: "#a371f7",
};
const KIND_RADIUS: Record<GraphNode["kind"], number> = { Patient: 6, Organization: 13, Practitioner: 10, Address: 7, Phone: 7 };

type SimNode = GraphNode & SimulationNodeDatum;
type SimLink = SimulationLinkDatum<SimNode> & { type: string };

const W = 960;
const H = 600;

export default function ForceGraph({ nodes, links }: { nodes: GraphNode[]; links: GraphLink[] }) {
  const [hover, setHover] = useState<SimNode | null>(null);

  const layout = useMemo(() => {
    const simNodes: SimNode[] = nodes.map((n) => ({ ...n }));
    const simLinks: SimLink[] = links.map((l) => ({ ...l }));
    const sim = forceSimulation(simNodes)
      .force("link", forceLink<SimNode, SimLink>(simLinks).id((d) => d.id).distance((l) => (l.type === "REFERRED_TO" ? 110 : 70)))
      .force("charge", forceManyBody().strength(-340))
      .force("center", forceCenter(W / 2, H / 2))
      .force("collide", forceCollide<SimNode>().radius((d) => KIND_RADIUS[d.kind] + 3))
      .stop();
    for (let i = 0; i < 300; i++) sim.tick();
    return { simNodes, simLinks };
  }, [nodes, links]);

  // Fit everything into the viewBox, whatever the layout's extent.
  const xs = layout.simNodes.map((n) => n.x ?? 0);
  const ys = layout.simNodes.map((n) => n.y ?? 0);
  const pad = 30;
  const minX = Math.min(...xs) - pad, maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad, maxY = Math.max(...ys) + pad;

  return (
    <div style={{ position: "relative" }}>
      <svg className="graph-box" viewBox={`${minX} ${minY} ${maxX - minX} ${maxY - minY}`} role="img" aria-label="Fraud ring graph">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="18" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill={KIND_COLOR.Practitioner} />
          </marker>
        </defs>
        {layout.simLinks.map((l, i) => {
          const s = l.source as SimNode, t = l.target as SimNode;
          const ref = l.type === "REFERRED_TO";
          return (
            <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y}
              stroke={ref ? KIND_COLOR.Practitioner : "var(--line)"} strokeWidth={ref ? 2 : 1}
              markerEnd={ref ? "url(#arrow)" : undefined} />
          );
        })}
        {layout.simNodes.map((n) => (
          <g key={n.id} transform={`translate(${n.x},${n.y})`} onMouseEnter={() => setHover(n)} onMouseLeave={() => setHover(null)}>
            <circle r={KIND_RADIUS[n.kind]} fill={KIND_COLOR[n.kind]} stroke="var(--panel)" strokeWidth={1.5} />
            {(n.kind === "Organization" || n.kind === "Practitioner" || n.kind === "Phone") && (
              // The stroke drawn under the fill (paintOrder) gives labels a halo,
              // so they stay readable where they cross edges.
              <text y={-KIND_RADIUS[n.kind] - 4} textAnchor="middle" fontSize={10} fill="var(--ink)"
                stroke="var(--panel)" strokeWidth={3} paintOrder="stroke">{n.label}</text>
            )}
          </g>
        ))}
      </svg>
      {hover && (
        <div className="panel" style={{ position: "absolute", top: 8, left: 8, padding: "6px 10px", fontSize: 12 }}>
          <b>{hover.kind}</b>: {hover.label}
        </div>
      )}
    </div>
  );
}
