"use client";

// Pre-auth view: choose a patient (and optionally an encounter). The LangGraph
// pre-auth agent reads their FHIR bundle and returns a draft request. The page
// shows where each section came from: FHIR, the rule engine, or LLM + citations.
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, EncounterRow, inr, PatientRow, post, PreAuth } from "@/lib/api";
import { encounterTip, planTip, PREAUTH } from "@/lib/glossary";
import { useQueryParam } from "@/lib/useQueryParam";

export default function PreAuthPage() {
  const [patientId, setPatientId] = useQueryParam("patient");
  const [q, setQ] = useState("");
  const [patients, setPatients] = useState<PatientRow[]>([]);
  const [encounters, setEncounters] = useState<EncounterRow[]>([]);
  const [encounterId, setEncounterId] = useState("");
  const [draft, setDraft] = useState<PreAuth | null>(null);
  const [busy, setBusy] = useState(false);
  const [showFhir, setShowFhir] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const t = setTimeout(() => {
      api<PatientRow[]>(`/patients?limit=40&q=${encodeURIComponent(q)}`).then(setPatients).catch((e) => setError(String(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    setDraft(null);
    setEncounterId("");
    if (patientId) api<EncounterRow[]>(`/patients/${patientId}/encounters`).then(setEncounters).catch((e) => setError(String(e)));
  }, [patientId]);

  async function generate() {
    setBusy(true);
    setError("");
    try {
      setDraft(await post<PreAuth>("/preauth", { patient_id: patientId, encounter_id: encounterId || null }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const chk = draft?.coverage_check;

  return (
    <>
      <h1>Pre-authorisation draft</h1>
      <p className="lede">
        The agent reads the patient&apos;s FHIR record, maps it to the request form, checks the plan&apos;s rules, retrieves the
        relevant policy clauses, and drafts the justification. The output is a draft; a person submits it.
      </p>
      {error && <div className="alert bad">{error}</div>}

      <div className="grid two" style={{ marginBottom: 14 }}>
        <div className="panel">
          <div className="row" style={{ marginBottom: 8 }}>
            <h2 style={{ margin: 0 }} data-tip={PREAUTH.patient}>1 · Patient</h2>
            <span className="spacer" />
            <input placeholder="Search name" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="table-wrap" style={{ maxHeight: 260, overflowY: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th data-tip="The (fake) patient. Click a row to select them.">Name</th>
                  <th data-tip="Where the patient lives.">City</th>
                  <th className="num" data-tip="How many times this patient was admitted to hospital. Pre-approval matters most for these.">Inpatient stays</th>
                </tr>
              </thead>
              <tbody>
                {patients.map((p) => (
                  <tr key={p.patient_id} className="clickable" onClick={() => setPatientId(p.patient_id)}
                      style={p.patient_id === patientId ? { background: "var(--accent-soft)" } : undefined}>
                    <td>{p.name} <span className="muted">({p.gender[0]}, b. {p.birth_date.slice(0, 4)})</span></td>
                    <td>{p.city}</td>
                    <td className="num">{p.inpatient_stays}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="panel">
          <h2 data-tip={PREAUTH.encounter}>2 · Encounter</h2>
          {!patientId && <p className="muted">Select a patient first.</p>}
          {patientId && (
            <>
              <select value={encounterId} onChange={(e) => setEncounterId(e.target.value)} style={{ width: "100%" }}
                data-tip="A visit = one encounter (date · visit type · reason). AMB = outpatient, IMP = admitted, EMER = emergency.">
                <option value="">Auto: latest inpatient stay (else latest visit)</option>
                {encounters.map((e) => (
                  <option key={e.encounter_id} value={e.encounter_id}>{e.start} · {e.encounter_class} · {e.type}</option>
                ))}
              </select>
              <div style={{ marginTop: 12 }}>
                <button className="primary" onClick={generate} disabled={busy}
                  data-tip="The AI agent reads the record, checks the plan rules, finds the relevant policy text and drafts the form.">
                  {busy ? "Agent working…" : "Draft pre-auth"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {draft && (
        <div className="grid two">
          <div className="panel">
            <div className="row">
              <h2 style={{ margin: 0 }} data-tip="Request id for this draft.">{draft.request_id}</h2>
              <span className="badge warn" data-tip={PREAUTH.draft}>{draft.status}</span>
            </div>
            <h3 data-tip={PREAUTH.fhir}>Patient · from FHIR</h3>
            <dl className="kv">
              <dt>Name</dt><dd>{draft.patient.name} ({draft.patient.gender}, {draft.patient.age ?? "?"} y)</dd>
              <dt>Address</dt><dd>{draft.patient.address}</dd>
              <dt>Phone</dt><dd>{draft.patient.phone}</dd>
            </dl>
            <h3 data-tip="The patient's insurance policy.">Coverage</h3>
            {draft.coverage ? (
              <dl className="kv">
                <dt>Plan</dt><dd>{draft.coverage.plan_name} <span className="mono tipped" data-tip={planTip(draft.coverage.plan_code)}>({draft.coverage.plan_code})</span></dd>
                <dt>Insurer</dt><dd>{draft.coverage.insurer}</dd>
                <dt>Policy no.</dt><dd className="mono">{draft.coverage.policy_number}</dd>
                <dt data-tip="Date the policy started. Matters for pre-existing-disease waiting periods.">Since</dt>
                <dd>{draft.coverage.policy_start}{draft.coverage.synthesized &&
                  <span className="muted tipped" data-tip="Synthea's US records have no Indian insurance, so the data parser assigned one of our fictional plans."> (assigned by parser)</span>}</dd>
                <dt data-tip="The most the plan pays in a year.">Sum insured</dt><dd>{inr(draft.coverage.sum_insured_inr)}</dd>
              </dl>
            ) : <p className="alert bad">No coverage on file.</p>}
            <h3 data-tip={PREAUTH.fhir}>Encounter · from FHIR</h3>
            <dl className="kv">
              <dt>Type</dt><dd>{draft.encounter.type} <span className="tipped" data-tip={encounterTip(draft.encounter.encounter_class)}>({draft.encounter.encounter_class})</span></dd>
              <dt>Dates</dt><dd>{draft.encounter.start} → {draft.encounter.end}</dd>
              <dt>Provider</dt><dd>{draft.encounter.provider_org}</dd>
              <dt>Doctor</dt><dd>{draft.encounter.treating_doctor}</dd>
            </dl>
            <h3 data-tip={PREAUTH.fhir}>Diagnoses · from FHIR</h3>
            <ul className="clean">
              {draft.diagnoses.map((d, i) => (
                <li key={i}>
                  {d.is_primary && <span className="badge info" data-tip="The main reason for this visit.">primary</span>} {d.display}{" "}
                  <span className="mono muted tipped" data-tip="SNOMED CT: the international standard code for this diagnosis.">SNOMED {d.code}</span>
                  {" · "}<span className="tipped" data-tip="Date this illness was FIRST diagnosed. Compared with the policy start for pre-existing-disease rules.">first seen {d.onset ?? "?"}</span>
                </li>
              ))}
            </ul>
            <h3 data-tip="The treatment being requested and its expected cost, taken from the record.">Requested service</h3>
            <p style={{ margin: 0 }}>{draft.requested_service} <span className="mono muted">{draft.requested_service_code}</span> · <b>{inr(draft.estimated_cost_inr)}</b></p>
          </div>

          <div className="grid" style={{ alignContent: "start" }}>
            {chk && (
              <div className="panel">
                <h2 data-tip={PREAUTH.rules}>Coverage check · rule engine</h2>
                <div className="row" style={{ marginBottom: 8 }}>
                  <span className={`badge ${chk.covered ? "good" : "bad"}`} data-tip={PREAUTH.covered}>{chk.covered ? "covered" : "likely not covered"}</span>
                  <span className={`badge ${chk.preauth_required ? "warn" : "good"}`} data-tip={PREAUTH.preauthRequired}>pre-auth {chk.preauth_required ? "required" : "not required"}</span>
                  <span className="badge info" data-tip={PREAUTH.category}>{chk.category}</span>
                  {chk.sublimit_inr && <span className="badge warn" data-tip={PREAUTH.sublimit}>sub-limit {inr(chk.sublimit_inr)}</span>}
                </div>
                <p className="muted" style={{ margin: 0 }}><span className="tipped" data-tip={PREAUTH.ped}>PED</span>: {chk.ped_status}</p>
                {chk.violations.map((v, i) => <div key={i} className="alert bad"><b>{v.code}</b> {v.detail}</div>)}
              </div>
            )}
            <div className="panel">
              <h2 data-tip={PREAUTH.justification}>Justification · {draft.generated_by}</h2>
              <h3 data-tip={PREAUTH.required}>Required by policy</h3>
              <ul className="clean">{draft.required_justification.map((x, i) => <li key={i}>{x}</li>)}</ul>
              <h3 data-tip={PREAUTH.necessity}>Medical necessity (draft)</h3>
              <p style={{ marginTop: 0 }}>{draft.clinical_justification}</p>
              {draft.missing_information.length > 0 && (
                <>
                  <h3 data-tip={PREAUTH.missing}>Missing before submission</h3>
                  <ul className="clean">{draft.missing_information.map((x, i) => <li key={i}>{x}</li>)}</ul>
                </>
              )}
              <h3 data-tip={PREAUTH.citations}>Citations</h3>
              {draft.citations.map((c) => (
                <div key={c.chunk_id}>
                  <span className="cite" data-tip={`${c.section}\n\n“${c.quote.replace(/^#+\s.*\n/gm, "").trim()}”`}>{c.chunk_id}</span>{" "}
                  <span className="muted">{c.section}</span>
                </div>
              ))}
            </div>
            <div className="panel">
              <div className="row">
                <h2 style={{ margin: 0 }} data-tip={PREAUTH.fhirClaim}>FHIR Claim (use = preauthorization)</h2>
                <span className="spacer" />
                <button onClick={() => setShowFhir(!showFhir)}>{showFhir ? "hide" : "show"}</button>
              </div>
              {showFhir && <pre className="code">{JSON.stringify(draft.fhir_claim, null, 2)}</pre>}
              {draft.decision_id && (
                <p className="muted" style={{ marginBottom: 0 }}>
                  Audit: <Link href={`/audit?subject=${draft.patient.patient_id}`} className="mono" data-tip="This draft was saved to the audit log. Click to see it.">{draft.decision_id}</Link>
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
