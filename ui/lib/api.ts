// lib/api.ts — typed client for the FastAPI backend.
//
// Every page goes through `api()`, so error handling and the `/api` prefix live
// in one place. The types mirror the Python response shapes. If you change a
// response in api/main.py, update the matching type here.

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = await res.text();
    try {
      detail = JSON.parse(detail).detail ?? detail;
    } catch {
      /* body was not JSON */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

// ---------------------------------------------------------------- shapes
export interface Health {
  status: string;
  llm_provider: string;
  neo4j: boolean;
  models_trained: boolean;
  rows: Record<string, number>;
}

export interface ClaimRow {
  claim_id: string;
  patient_id: string;
  name: string;
  created: string;
  total_inr: number;
  encounter_class: string;
  category: string;
  dx_display: string | null;
  plan_code: string;
  denied: number;
  denial_reason: string;
  denial_wrongful: number;
  denial_risk: number;
  fraud_score: number;
}

export interface Reason {
  code: string;
  label: string;
  p_given_denied: number;
  probability: number;
}

export interface Driver {
  feature: string;
  description: string;
  value: number;
  contribution: number;
  direction: "raises" | "lowers";
  hint_reason: string | null;
}

export interface DenialResult {
  claim_id: string;
  denial_risk: number;
  risk_band: "LOW" | "MEDIUM" | "HIGH";
  top_reasons: Reason[];
  drivers: Driver[];
  actual: { denied: number; reason: string; wrongful: number };
  model_version: string;
  reason_model_version: string;
  claim: Record<string, string | number | null>;
  decision_id: string;
}

export interface Citation {
  chunk_id: string;
  source: string;
  section: string;
  quote: string;
}

export interface AppealResult {
  claim_id: string;
  denial: { code: string; label: string; source: string; risk: number };
  grounds: string[];
  appeal_strength: "STRONG" | "WEAK" | "NEEDS_REVIEW";
  letter: string;
  citations: Citation[];
  trace: { queries: string[]; retrieval_attempts: number; revisions: number; unresolved_issues: string[] };
  generated_by: string;
  created_at: string;
  decision_id: string;
}

export interface PatientRow {
  patient_id: string;
  name: string;
  gender: string;
  birth_date: string;
  city: string;
  inpatient_stays: number;
}

export interface EncounterRow {
  encounter_id: string;
  encounter_class: string;
  type: string;
  start: string;
  reason_code: string | null;
}

export interface PreAuth {
  request_id: string;
  created_at: string;
  status: string;
  patient: { patient_id: string; name: string; gender: string; birth_date: string; age: number | null; phone: string; address: string };
  coverage: {
    coverage_id: string; policy_number: string | null; plan_code: string; plan_name: string; insurer: string;
    policy_start: string; sum_insured_inr: number; synthesized: boolean;
  } | null;
  encounter: { encounter_id: string; encounter_class: string; type: string; start: string; end: string; provider_org: string; treating_doctor: string };
  diagnoses: { code: string | null; system: string; display: string; onset: string | null; is_primary: boolean }[];
  requested_service: string;
  requested_service_code: string | null;
  estimated_cost_inr: number;
  coverage_check: {
    covered: boolean; preauth_required: boolean; category: string; sublimit_inr: number | null;
    ped_status: string; violations: { code: string; reason: string; detail: string }[];
  } | null;
  required_justification: string[];
  clinical_justification: string;
  missing_information: string[];
  citations: Citation[];
  generated_by: string;
  decision_id: string | null;
  fhir_claim: unknown;
}

export interface GraphNode {
  id: string;
  kind: "Patient" | "Organization" | "Practitioner" | "Address" | "Phone";
  label: string;
}
export interface GraphLink {
  source: string;
  target: string;
  type: string;
}

export interface Ring {
  ring_id: string;
  community: number;
  ring_score: number;
  components: Record<string, number>;
  evidence: string[];
  counts: { patients: number; clinics: number; doctors: number; claims: number; billed_inr: number };
  patient_ids: string[];
  claim_ids: string[];
  graph: { nodes: GraphNode[]; links: GraphLink[] };
}

export interface RingsResponse {
  backend: string;
  threshold: number;
  cypher_pattern_hits: Record<string, number>;
  evaluation: { patient_precision?: number; patient_recall?: number; overlap_with_planted_rings?: Record<string, Record<string, number>> };
  rings: Ring[];
}

export interface ProcessResult {
  claim_id: string;
  outcome: string;
  steps: { step: string; summary: string; decision_id: string | null }[];
  decision_id: string;
}

export interface AuditRecord {
  seq: number;
  decision_id: string;
  created_at: string;
  module: string;
  subject_type: string;
  subject_id: string;
  recommendation: string;
  model_name: string | null;
  model_version: string | null;
  llm_provider: string | null;
  llm_model: string | null;
  inputs: unknown;
  retrieval: unknown;
  output: unknown;
  prev_hash: string;
  record_hash: string;
}

// ---------------------------------------------------------------- formatting
export const inr = (n: number) =>
  "₹" + Math.round(n).toLocaleString("en-IN");

export const pct = (p: number) => `${Math.round(p * 100)}%`;

export const short = (id: string) => id.slice(0, 8);
