"use client";

// Fraud-ring view: ring candidates found by community detection + pattern
// scoring (graph/graph_analytics.py), with each ring's subgraph drawn as a
// force-directed network. The evaluation line compares against the planted
// answer key, shown here only because this is a learning project.
import { useEffect, useState } from "react";
import ForceGraph, { KIND_COLOR } from "@/components/ForceGraph";
import Meter from "@/components/Meter";
import { api, inr, Ring, RingsResponse } from "@/lib/api";
import { COMPONENT, FRAUD, NODE_KIND } from "@/lib/glossary";

const COMPONENT_LABEL: Record<string, string> = {
  identity: "Shared phone / address",
  velocity: "Bursty claim velocity",
  doc_mesh: "Doctors across 3+ clinics",
  referral_loop: "Closed referral loop",
  structuring: "Just-below-threshold amounts",
};

export default function FraudPage() {
  const [data, setData] = useState<RingsResponse | null>(null);
  const [sel, setSel] = useState<Ring | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<RingsResponse>("/fraud/rings")
      .then((d) => {
        setData(d);
        setSel(d.rings[0] ?? null);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const ev = data?.evaluation;

  return (
    <>
      <h1>Fraud rings</h1>
      <p className="lede">
        Louvain community detection over patients, clinics, doctors, phones and addresses, then each community is
        scored on five collusion patterns. A ring usually lights up 2–3 of them. Hover a node for its name; gold
        arrows are referrals.
      </p>
      {error && <div className="alert bad">{error}</div>}
      {data && (
        <div className="row" style={{ marginBottom: 14 }}>
          <span className="badge info" data-tip={FRAUD.backend}>backend: {data.backend}</span>
          <span className="badge info" data-tip={FRAUD.threshold}>flag threshold: {data.threshold}</span>
          {ev?.patient_precision !== undefined && (
            <span className="muted tipped" data-tip={FRAUD.eval}>
              vs planted answer key → precision <b>{ev.patient_precision}</b>, recall <b>{ev.patient_recall}</b>
            </span>
          )}
        </div>
      )}
      {data && data.rings.length === 0 && <div className="alert warn">No communities above the threshold.</div>}

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", marginBottom: 14 }}>
        {data?.rings.map((r) => (
          <div key={r.ring_id} className="panel" style={{ cursor: "pointer", borderColor: sel?.ring_id === r.ring_id ? "var(--accent)" : undefined }}
               onClick={() => setSel(r)}>
            <div className="row">
              <h2 style={{ margin: 0 }}>{r.ring_id}</h2>
              <span className="badge bad" data-tip={FRAUD.score}>score {r.ring_score.toFixed(2)}</span>
              <span className="spacer" />
              <span className="muted tipped" data-tip={FRAUD.billed}>{inr(r.counts.billed_inr)} billed</span>
            </div>
            <p className="muted" style={{ margin: "6px 0" }}>
              {r.counts.patients} patients · {r.counts.clinics} clinics · {r.counts.doctors} doctors · {r.counts.claims} claims
            </p>
            <ul className="clean">{r.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
          </div>
        ))}
      </div>

      {sel && (
        <div className="grid two">
          <div className="panel">
            <h2 data-tip="The group drawn as a map: dots are people, clinics, phones and addresses; lines are connections (visited, treated by, lives at, referred to). Hover a dot for its name.">{sel.ring_id} network</h2>
            <div className="legend" style={{ marginBottom: 8 }}>
              {Object.entries(KIND_COLOR).map(([k, c]) => (
                <span key={k} style={{ ["--c" as string]: c }} data-tip={NODE_KIND[k]}>{k}</span>
              ))}
            </div>
            <ForceGraph nodes={sel.graph.nodes} links={sel.graph.links} />
          </div>
          <div className="panel">
            <h2 data-tip="How strongly this group shows each of the 5 fraud patterns (0–100%). Hover a pattern name.">Pattern scores</h2>
            {Object.entries(sel.components).map(([k, v]) => (
              <div key={k} style={{ marginBottom: 8 }}>
                <div><span className="tipped" data-tip={COMPONENT[k]}>{COMPONENT_LABEL[k] ?? k}</span></div>
                <Meter value={v} label={COMPONENT_LABEL[k] ?? k} />
              </div>
            ))}
            <p className="muted">Ring score = mean of the two strongest patterns.</p>
            {ev?.overlap_with_planted_rings?.[sel.ring_id] && (
              <>
                <h3 data-tip={FRAUD.overlap}>Overlap with planted rings (evaluation only)</h3>
                <ul className="clean">
                  {Object.entries(ev.overlap_with_planted_rings[sel.ring_id]).map(([g, n]) => <li key={g}>{g}: {n} patients</li>)}
                </ul>
              </>
            )}
            {Object.keys(data?.cypher_pattern_hits ?? {}).length > 0 && (
              <>
                <h3 data-tip={FRAUD.cypher}>Cypher pattern queries (whole graph)</h3>
                <ul className="clean">
                  {Object.entries(data!.cypher_pattern_hits).map(([q, n]) => <li key={q}><span className="mono">{q}</span>: {n} hits</li>)}
                </ul>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
