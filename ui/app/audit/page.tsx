"use client";

// Audit log view: every decision record, newest first, and the hash-chain
// integrity check. Click a row to see the exact inputs, retrieval context and
// output that were stored for that decision.
import { Fragment, useEffect, useState } from "react";
import { api, AuditRecord, short } from "@/lib/api";
import { useQueryParam } from "@/lib/useQueryParam";

const MODULES = ["", "orchestrator", "fraud", "denial", "preauth", "appeal"];

export default function AuditPage() {
  const [subject, setSubject] = useQueryParam("subject");
  const [module, setModule] = useState("");
  const [rows, setRows] = useState<AuditRecord[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [chain, setChain] = useState<{ valid: boolean; records_checked: number; first_broken_seq?: number; head_hash?: string } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const p = new URLSearchParams({ limit: "200" });
    if (module) p.set("module", module);
    if (subject) p.set("subject_id", subject);
    api<AuditRecord[]>(`/audit?${p}`).then(setRows).catch((e) => setError(String(e)));
    api<typeof chain>("/audit/verify").then(setChain).catch(() => undefined);
  }, [module, subject]);

  return (
    <>
      <h1>Audit log</h1>
      <p className="lede">
        One record per decision: inputs, model version, LLM used, retrieved evidence, recommendation and timestamp. Each
        record&apos;s hash covers the previous record&apos;s hash, so any edit or deletion breaks the chain.
      </p>
      {error && <div className="alert bad">{error}</div>}
      {chain && (
        <div className={`alert ${chain.valid ? "good" : "bad"}`}>
          {chain.valid
            ? `Hash chain intact: ${chain.records_checked} records verified. Head ${chain.head_hash?.slice(0, 16)}…`
            : `Hash chain BROKEN at record #${chain.first_broken_seq}. The log was modified.`}
        </div>
      )}
      <div className="panel">
        <div className="row" style={{ marginBottom: 10 }}>
          <select value={module} onChange={(e) => setModule(e.target.value)}>
            {MODULES.map((m) => <option key={m} value={m}>{m || "all modules"}</option>)}
          </select>
          <input placeholder="Filter by claim / patient id" value={subject} onChange={(e) => setSubject(e.target.value.trim())} style={{ minWidth: 280 }} />
          <span className="muted">{rows.length} records</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>#</th><th>Time (UTC)</th><th>Module</th><th>Subject</th><th>Recommendation</th><th>Model version</th><th>LLM</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.decision_id}>
                  <tr className="clickable" onClick={() => setOpen(open === r.decision_id ? null : r.decision_id)}>
                    <td className="num">{r.seq}</td>
                    <td className="mono">{r.created_at.replace("T", " ").slice(0, 19)}</td>
                    <td><span className="badge info">{r.module}</span></td>
                    <td className="mono">{r.subject_type}:{short(r.subject_id)}</td>
                    <td><b>{r.recommendation}</b></td>
                    <td className="mono">{r.model_version ?? "—"}</td>
                    <td className="mono">{r.llm_provider ? `${r.llm_provider}/${r.llm_model}` : "—"}</td>
                  </tr>
                  {open === r.decision_id && (
                    <tr>
                      <td colSpan={7}>
                        <div className="grid three">
                          <div><h3>Inputs</h3><pre className="code">{JSON.stringify(r.inputs, null, 2)}</pre></div>
                          <div><h3>Retrieval context</h3><pre className="code">{JSON.stringify(r.retrieval, null, 2)}</pre></div>
                          <div><h3>Output</h3><pre className="code">{JSON.stringify(r.output, null, 2)}</pre></div>
                        </div>
                        <p className="mono muted">decision {r.decision_id}<br />prev {r.prev_hash}<br />hash {r.record_hash}</p>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
