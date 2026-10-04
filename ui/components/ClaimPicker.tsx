"use client";

// A compact claim chooser used by the Denial and Appeal pages when no
// ?claim=... is in the URL. Defaults to the "wrongful" view, the most
// interesting claims for an appeal demo.
import { useEffect, useState } from "react";
import { api, ClaimRow, inr, short } from "@/lib/api";
import Meter from "./Meter";

export default function ClaimPicker({ onPick, defaultView = "wrongful" }: { onPick: (id: string) => void; defaultView?: string }) {
  const [view, setView] = useState(defaultView);
  const [rows, setRows] = useState<ClaimRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ claims: ClaimRow[] }>(`/claims?view=${view}&limit=25`)
      .then((r) => setRows(r.claims))
      .catch((e) => setError(String(e)));
  }, [view]);

  return (
    <div className="panel">
      <div className="row" style={{ marginBottom: 10 }}>
        <h2 style={{ margin: 0 }}>Pick a claim</h2>
        <span className="spacer" />
        <div className="tabs">
          {["wrongful", "denied", "high_risk", "all"].map((v) => (
            <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>
              {v.replace("_", " ")}
            </button>
          ))}
        </div>
      </div>
      {error && <div className="alert bad">{error}</div>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Claim</th><th>Patient</th><th>Diagnosis</th><th className="num">Amount</th><th>Denial risk</th><th>Outcome</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.claim_id} className="clickable" onClick={() => onPick(r.claim_id)}>
                <td className="mono">{short(r.claim_id)}</td>
                <td>{r.name}</td>
                <td>{r.dx_display ?? "—"}</td>
                <td className="num">{inr(r.total_inr)}</td>
                <td><Meter value={r.denial_risk} /></td>
                <td>{r.denied ? <span className="badge bad">{r.denial_reason}</span> : <span className="badge good">approved</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
