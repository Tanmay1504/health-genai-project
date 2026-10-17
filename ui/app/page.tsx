"use client";

// Claims dashboard: every claim with its denial risk and fraud score, plus a
// "Run pipeline" button that sends one claim through the LangGraph
// orchestrator (fraud → pre-auth → denial → appeal) and shows the route taken.
// Hover anything with a dotted underline or "?" cursor for an explanation
// (texts live in lib/glossary.ts).
import Link from "next/link";
import { useEffect, useState } from "react";
import Meter from "@/components/Meter";
import { api, ClaimRow, Health, inr, post, ProcessResult, short } from "@/lib/api";
import { CLAIM_COL, encounterTip, OUTCOME, planTip, reasonTip, STATUS, STEP, VIEW, WRONGFUL } from "@/lib/glossary";

const VIEWS = [
  { key: "all", label: "All" },
  { key: "high_risk", label: "High denial risk" },
  { key: "fraud", label: "Suspected fraud" },
  { key: "denied", label: "Denied" },
  { key: "wrongful", label: "Wrongfully denied (ground truth)" },
];

const COLUMNS = ["Claim", "Patient", "Date", "Diagnosis", "Plan", "Amount", "Denial risk", "Fraud", "Insurer outcome", "Run"];

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
        written to the audit log. <span className="muted">Tip: hover anything underlined for an explanation.</span>
      </p>

      {health && (
        <div className="row" style={{ marginBottom: 14 }}>
          <span className="badge info" data-tip={STATUS.llm}>LLM: {health.llm_provider}</span>
          <span className={`badge ${health.neo4j ? "good" : "warn"}`} data-tip={health.neo4j ? STATUS.neo4jOn : STATUS.neo4jOff}>
            Neo4j: {health.neo4j ? "connected" : "offline"}
          </span>
          <span className={`badge ${health.models_trained ? "good" : "bad"}`} data-tip={STATUS.models}>
            models: {health.models_trained ? "trained" : "missing"}
          </span>
          <span className="muted">{health.rows.patients} patients · {health.rows.claims_labeled} claims</span>
        </div>
      )}
      {error && <div className="alert bad">{error}</div>}

      {result && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <div className="row">
            <h2 style={{ margin: 0 }}>Pipeline result for {short(result.claim_id)}</h2>
            <span className={`badge ${OUTCOME_STYLE[result.outcome] ?? "info"}`} data-tip={OUTCOME[result.outcome]}>{result.outcome}</span>
            <span className="spacer" />
            <Link href={`/audit?subject=${result.claim_id}`} data-tip="See every record this run wrote to the audit log.">audit trail →</Link>
          </div>
          <ol className="steps" style={{ marginTop: 12 }}>
            {result.steps.map((s, i) => (
              <li key={i}><b className="tipped" data-tip={STEP[s.step]}>{s.step}</b> <span className="muted">— {s.summary}</span></li>
            ))}
          </ol>
          {result.outcome.startsWith("APPEAL") && <p><Link href={`/appeal?claim=${result.claim_id}`}>Open the appeal letter →</Link></p>}
        </div>
      )}

      <div className="panel">
        <div className="row" style={{ marginBottom: 10 }}>
          <div className="tabs">
            {VIEWS.map((v) => (
              <button key={v.key} className={view === v.key ? "on" : ""} onClick={() => setView(v.key)} data-tip={VIEW[v.key]}>
                {v.label}
              </button>
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
                {COLUMNS.map((c) => (
                  <th key={c} className={c === "Amount" ? "num" : undefined} data-tip={CLAIM_COL[c]}>{c === "Run" ? "" : c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.claims.map((c) => (
                <tr key={c.claim_id}>
                  <td className="mono"><Link href={`/denial?claim=${c.claim_id}`} data-tip={`Claim ${c.claim_id}\nClick to see why the model predicts this risk.`}>{short(c.claim_id)}</Link></td>
                  <td>{c.name}</td>
                  <td className="nowrap">{c.created}</td>
                  <td>
                    {c.dx_display ?? <span data-tip="No diagnosis recorded on this claim (e.g. a routine check-up or dental visit).">—</span>}{" "}
                    <span className="muted tipped" data-tip={encounterTip(c.encounter_class)}>({c.encounter_class})</span>
                  </td>
                  <td className="mono"><span className="tipped" data-tip={planTip(c.plan_code)}>{c.plan_code}</span></td>
                  <td className="num">{inr(c.total_inr)}</td>
                  <td><Meter value={c.denial_risk} label="Chance the insurer rejects this claim" /></td>
                  <td><Meter value={c.fraud_score} warnAt={0.4} badAt={0.7} label="Chance this claim is part of a fraud ring" /></td>
                  <td>
                    {c.denied
                      ? <span className="badge bad" data-tip={reasonTip(c.denial_reason)}>{c.denial_reason}</span>
                      : <span className="badge good" data-tip={reasonTip("APPROVED")}>approved</span>}
                    {c.denial_wrongful ? <span className="badge warn" style={{ marginLeft: 4 }} data-tip={WRONGFUL}>wrongful</span> : null}
                  </td>
                  <td>
                    <button onClick={() => run(c.claim_id)} disabled={running !== null} data-tip={CLAIM_COL.Run}>
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
