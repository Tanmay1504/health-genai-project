"use client";

// Claims dashboard: every claim with its denial risk and fraud score, plus a
// "Run pipeline" button that sends one claim through the LangGraph
// orchestrator (fraud → pre-auth → denial → appeal) and shows the route taken.
import Link from "next/link";
import { useEffect, useState } from "react";
import Meter from "@/components/Meter";
import { api, ClaimRow, Health, inr, post, ProcessResult, short } from "@/lib/api";

const VIEWS = [
  { key: "all", label: "All" },
  { key: "high_risk", label: "High denial risk" },
  { key: "fraud", label: "Suspected fraud" },
  { key: "denied", label: "Denied" },
  { key: "wrongful", label: "Wrongfully denied (ground truth)" },
];

const OUTCOME_STYLE: Record<string, string> = {
  SUBMIT: "good", HOLD_REFER_TO_SIU: "bad", FIX_AND_RESUBMIT: "warn",
  APPEAL_STRONG: "info", APPEAL_NEEDS_REVIEW: "warn", APPEAL_WEAK: "warn",
};

export default function ClaimsPage() {
  const [health, setHealth] = useState<Health | null>(null);
  const [view, setView] = useState("all");
  const [q, setQ] = useState("");
  const [data, setData] = useState<{ total: number; claims: ClaimRow[] } | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState<string | null>(null);
  const [result, setResult] = useState<ProcessResult | null>(null);

  useEffect(() => {
    api<Health>("/health").then(setHealth).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      api<{ total: number; claims: ClaimRow[] }>(`/claims?view=${view}&limit=150&q=${encodeURIComponent(q)}`)
        .then(setData)
        .catch((e) => setError(String(e)));
    }, 250); // debounce typing in the search box
    return () => clearTimeout(t);
  }, [view, q]);

  async function run(claimId: string) {
    setRunning(claimId);
    setResult(null);
    try {
      setResult(await post<ProcessResult>(`/process/${claimId}`));
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(null);
    }
  }

  return (
    <>
      <h1>Claims</h1>
      <p className="lede">
        Every synthetic claim, scored by the denial model and the graph-aware fraud model. <b>Run</b> sends a claim
        through the orchestrator: fraud screen → pre-auth gate → denial prediction → appeal or fix. Each step is
        written to the audit log.
      </p>

      {health && (
        <div className="row" style={{ marginBottom: 14 }}>
          <span className="badge info">LLM: {health.llm_provider}</span>
          <span className={`badge ${health.neo4j ? "good" : "warn"}`}>Neo4j: {health.neo4j ? "connected" : "offline"}</span>
          <span className={`badge ${health.models_trained ? "good" : "bad"}`}>models: {health.models_trained ? "trained" : "missing"}</span>
          <span className="muted">{health.rows.patients} patients · {health.rows.claims_labeled} claims</span>
        </div>
      )}
      {error && <div className="alert bad">{error}</div>}

      {result && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <div className="row">
            <h2 style={{ margin: 0 }}>Pipeline result for {short(result.claim_id)}</h2>
            <span className={`badge ${OUTCOME_STYLE[result.outcome] ?? "info"}`}>{result.outcome}</span>
            <span className="spacer" />
            <Link href={`/audit?subject=${result.claim_id}`}>audit trail →</Link>
          </div>
          <ol className="steps" style={{ marginTop: 12 }}>
            {result.steps.map((s, i) => (
              <li key={i}><b>{s.step}</b> <span className="muted">— {s.summary}</span></li>
            ))}
          </ol>
          {result.outcome.startsWith("APPEAL") && <p><Link href={`/appeal?claim=${result.claim_id}`}>Open the appeal letter →</Link></p>}
        </div>
      )}

      <div className="panel">
        <div className="row" style={{ marginBottom: 10 }}>
          <div className="tabs">
            {VIEWS.map((v) => (
              <button key={v.key} className={view === v.key ? "on" : ""} onClick={() => setView(v.key)}>{v.label}</button>
            ))}
          </div>
          <span className="spacer" />
          <input placeholder="Search name, diagnosis, claim id" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="muted" style={{ marginBottom: 6 }}>{data ? `${data.total} claims (showing up to 150)` : "Loading…"}</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Claim</th><th>Patient</th><th>Date</th><th>Diagnosis</th><th>Plan</th>
                <th className="num">Amount</th><th>Denial risk</th><th>Fraud</th><th>Insurer outcome</th><th></th>
              </tr>
            </thead>
            <tbody>
              {data?.claims.map((c) => (
                <tr key={c.claim_id}>
                  <td className="mono"><Link href={`/denial?claim=${c.claim_id}`}>{short(c.claim_id)}</Link></td>
                  <td>{c.name}</td>
                  <td className="nowrap">{c.created}</td>
                  <td>{c.dx_display ?? "—"} <span className="muted">({c.encounter_class})</span></td>
                  <td className="mono">{c.plan_code}</td>
                  <td className="num">{inr(c.total_inr)}</td>
                  <td><Meter value={c.denial_risk} /></td>
                  <td><Meter value={c.fraud_score} warnAt={0.4} badAt={0.7} /></td>
                  <td>
                    {c.denied ? <span className="badge bad">{c.denial_reason}</span> : <span className="badge good">approved</span>}
                    {c.denial_wrongful ? <span className="badge warn" style={{ marginLeft: 4 }}>wrongful</span> : null}
                  </td>
                  <td>
                    <button onClick={() => run(c.claim_id)} disabled={running !== null}>
                      {running === c.claim_id ? "Running…" : "Run"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
