"use client";

// Denial risk view: P(denied), the top reason codes P(reason) = P(denied) × P(reason | denied),
// and the SHAP drivers. Each driver's signed contribution shows which facts
// about the claim pushed the risk up (red) or down (green).
import Link from "next/link";
import { useEffect, useState } from "react";
import ClaimPicker from "@/components/ClaimPicker";
import Meter from "@/components/Meter";
import { api, DenialResult, inr, short } from "@/lib/api";
import { useQueryParam } from "@/lib/useQueryParam";

export default function DenialPage() {
  const [claimId, setClaimId] = useQueryParam("claim");
  const [d, setD] = useState<DenialResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!claimId) return;
    setD(null);
    setError("");
    api<DenialResult>(`/denial/${claimId}`).then(setD).catch((e) => setError(String(e)));
  }, [claimId]);

  if (!claimId) {
    return (
      <>
        <h1>Denial risk</h1>
        <p className="lede">Choose a claim to see its denial probability, the most likely reason codes and the evidence behind them.</p>
        <ClaimPicker onPick={setClaimId} defaultView="high_risk" />
      </>
    );
  }

  const maxAbs = d ? Math.max(...d.drivers.map((x) => Math.abs(x.contribution)), 0.01) : 1;
  const band = d?.risk_band === "HIGH" ? "bad" : d?.risk_band === "MEDIUM" ? "warn" : "good";

  return (
    <>
      <div className="row">
        <h1>Denial risk · <span className="mono">{short(claimId)}</span></h1>
        <span className="spacer" />
        <button onClick={() => setClaimId("")}>← choose another claim</button>
      </div>
      {error && <div className="alert bad">{error}</div>}
      {!d && !error && <p className="muted">Scoring…</p>}
      {d && (
        <>
          <div className="grid three" style={{ marginBottom: 14 }}>
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>Probability of denial</h3>
              <div className="big-number">{Math.round(d.denial_risk * 100)}%</div>
              <div style={{ marginTop: 8 }}><span className={`badge ${band}`}>{d.risk_band} RISK</span></div>
              <p className="muted mono" style={{ marginBottom: 0 }}>{d.model_version}</p>
            </div>
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>Top reason codes</h3>
              {d.top_reasons.map((r) => (
                <div key={r.code} style={{ marginBottom: 8 }}>
                  <div><b>{r.code}</b> {r.label}</div>
                  <Meter value={r.probability} />
                </div>
              ))}
            </div>
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>Claim</h3>
              <dl className="kv">
                <dt>Patient</dt><dd>{String(d.claim.name)}</dd>
                <dt>Diagnosis</dt><dd>{String(d.claim.dx_display ?? "—")}</dd>
                <dt>Procedure</dt><dd>{String(d.claim.procedure ?? "—")}</dd>
                <dt>Amount</dt><dd>{inr(Number(d.claim.total_inr))}</dd>
                <dt>Plan</dt><dd className="mono">{String(d.claim.plan_code)}</dd>
                <dt>Cover since</dt><dd>{String(d.claim.coverage_start)}</dd>
                <dt>Dx first seen</dt><dd>{String(d.claim.dx_onset ?? "—")}</dd>
                <dt>Insurer said</dt>
                <dd>
                  {d.actual.denied ? <span className="badge bad">denied {d.actual.reason}</span> : <span className="badge good">approved</span>}
                  {d.actual.wrongful ? <span className="badge warn" style={{ marginLeft: 4 }}>wrongful (ground truth)</span> : null}
                </dd>
              </dl>
            </div>
          </div>

          <div className="panel">
            <div className="row">
              <h2 style={{ margin: 0 }}>Why — SHAP drivers</h2>
              <span className="spacer" />
              {d.denial_risk >= 0.5 && <Link href={`/appeal?claim=${claimId}`}>Draft an appeal →</Link>}
            </div>
            <p className="muted">Exact TreeSHAP contributions (log-odds). Red pushes toward denial, green away from it.</p>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Feature</th><th className="num">Value</th><th style={{ width: "40%" }}>Contribution</th><th className="num">log-odds</th><th>Hints at</th></tr></thead>
                <tbody>
                  {d.drivers.map((x) => {
                    const w = `${(Math.abs(x.contribution) / maxAbs) * 100}%`;
                    return (
                      <tr key={x.feature}>
                        <td>{x.description} <span className="muted mono">{x.feature}</span></td>
                        <td className="num">{x.value}</td>
                        <td>
                          <div className="contrib">
                            <div>{x.contribution < 0 && <div className="neg" style={{ width: w }} />}</div>
                            <div>{x.contribution > 0 && <div className="pos" style={{ width: w }} />}</div>
                          </div>
                        </td>
                        <td className="num">{x.contribution > 0 ? "+" : ""}{x.contribution.toFixed(2)}</td>
                        <td>{x.hint_reason ? <span className="badge info">{x.hint_reason}</span> : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="muted" style={{ marginBottom: 0 }}>
              Audit record: <Link href={`/audit?subject=${claimId}`} className="mono">{d.decision_id}</Link>
            </p>
          </div>
        </>
      )}
    </>
  );
}
