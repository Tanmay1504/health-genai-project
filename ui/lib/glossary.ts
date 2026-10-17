// lib/glossary.ts — every hover tooltip in the UI, in plain language.
//
// Keeping all explanations in one file means the wording stays consistent
// across pages, and you can read this file as a glossary of the whole system.
// The numbers here mirror data/reference.py; update both together.

const lookup = (table: Record<string, string>, key: string | null | undefined) =>
  (key && table[key]) || "";

// ---------------------------------------------------------------- codes
export const REASON: Record<string, string> = {
  D01: "D01 · Pre-authorisation not obtained\nThe plan needed the insurer's approval BEFORE treatment (every hospital stay, or bills above the plan's limit), and none was taken.",
  D02: "D02 · Not covered by the plan\nThis treatment isn't included in the patient's plan, e.g. maternity on Silver, or ordinary clinic visits on the hospital-only JAC scheme.",
  D03: "D03 · Pre-existing disease waiting period\nThe illness existed BEFORE the policy started, and the waiting period (Silver 36 months, Gold 24) isn't over yet.\nIt is wrongly applied if the illness was first found after the policy started.",
  D04: "D04 · Missing documents\nDischarge summary, itemised bill or test reports were not attached.",
  D05: "D05 · Over the sub-limit\nThe bill is above the plan's cap for this kind of treatment, e.g. Silver pays at most ₹25,000 for cataract.",
  D06: "D06 · Filed too late\nClaims must be submitted within 30 days of treatment.",
  D07: "D07 · Suspected fraud\nThe insurer's investigation unit flagged this claim.",
  APPROVED: "Approved\nThe insurer paid this claim.",
};
export const reasonTip = (code: string) => lookup(REASON, code);

export const WRONGFUL =
  "Wrongful denial (ground truth)\nThe insurer rejected this claim by MISTAKE. We know because the data is simulated. These are the claims the appeal agent should win.";

export const PLAN: Record<string, string> = {
  "SGH-SILVER": "Suraksha Silver (fictional plan)\n• ₹3 lakh cover per year\n• Old illnesses: 36-month waiting period\n• Pre-approval: every hospital stay, or bills over ₹25,000\n• Maternity NOT covered\n• Caps: cataract ₹25,000 · orthopaedic ₹1.5 lakh · cardiac ₹2 lakh",
  "SGH-GOLD": "Suraksha Gold (fictional family plan)\n• ₹10 lakh cover per year\n• Old illnesses: 24-month waiting period\n• Pre-approval: every hospital stay, or bills over ₹50,000\n• Maternity covered up to ₹1 lakh · cataract up to ₹60,000",
  "JAC-BASIC": "Jan Arogya Cover (fictional government-style scheme)\n• ₹5 lakh cover\n• Hospital stays ONLY: ordinary clinic visits are not covered (except cataract, minor surgery, dialysis)\n• Old illnesses covered from day one\n• Pre-approval: every hospital stay, or bills over ₹20,000",
};
export const planTip = (code: string) => lookup(PLAN, code) || `Insurance plan ${code}`;

export const ENCOUNTER: Record<string, string> = {
  AMB: "AMB · Outpatient\nThe patient visited and went home the same day.",
  IMP: "IMP · Inpatient\nThe patient was admitted to hospital (overnight stay).",
  EMER: "EMER · Emergency visit.",
  VR: "VR · Virtual / tele-consultation.",
  HH: "HH · Home-health visit.",
  WELL: "WELL · Wellness / routine check-up.",
};
export const encounterTip = (code: string) => lookup(ENCOUNTER, code) || `Visit type ${code}`;

// ---------------------------------------------------------------- navigation + status
export const NAV: Record<string, string> = {
  "/": "Claims\nEvery claim with its rejection risk and fraud score. Click Run to send a claim through the whole system.",
  "/preauth": "Pre-auth\nPick a patient. The AI reads their medical record and fills in the insurer's pre-approval form, with reasons.",
  "/fraud": "Fraud rings\nGangs of patients, doctors and clinics found by mapping who is connected to whom.",
  "/denial": "Denial risk\nFor one claim: how likely a rejection is, the likely reason, and what drives the prediction.",
  "/appeal": "Appeal\nThe AI writes a letter asking the insurer to pay a wrongly rejected claim, quoting the exact policy rules.",
  "/audit": "Audit log\nA tamper-proof record of every decision the system made: what, when, and by which model.",
};

export const STATUS = {
  llm: "Which AI writes the letters and form text. Change it with LLM_PROVIDER in .env.",
  neo4jOn: "Neo4j (the graph database used for fraud detection) is running.",
  neo4jOff: "Neo4j is not running. Fraud results still show (they were saved earlier), but live graph queries won't work.",
  models: "The prediction models (denial risk, reasons, fraud) are trained and loaded.",
};

// ---------------------------------------------------------------- claims page
export const VIEW: Record<string, string> = {
  all: "Every claim, highest rejection risk first.",
  high_risk: "Claims our model thinks have a 50%+ chance of being rejected.",
  fraud: "Claims our model thinks have a 50%+ chance of belonging to a fraud ring.",
  denied: "Claims the insurer actually rejected.",
  wrongful: "Claims the insurer rejected by mistake (known only because the data is simulated). Best ones to try Appeal on.",
};

export const CLAIM_COL: Record<string, string> = {
  Claim: "Short id of the claim. Click it to see why the model predicts what it does.",
  Patient: "The (fake) patient who received the treatment.",
  Date: "Date of the treatment.",
  Diagnosis: "What the patient was treated for. '—' means no diagnosis was recorded (e.g. a routine check-up). The bracket is the visit type; hover it.",
  Plan: "The patient's insurance plan. Hover a plan code for its rules.",
  Amount: "The bill, in rupees.",
  "Denial risk": "Our model's prediction of how likely the insurer is to REJECT this claim.",
  Fraud: "Our model's prediction of how likely this claim is part of a fraud ring. It uses the connection map: shared phones, doctors at many clinics, referral loops.",
  "Insurer outcome": "What the (simulated) insurer actually decided. Hover a code for its meaning.",
  Run: "Send this claim through the whole pipeline: fraud check → pre-approval check → rejection prediction → appeal or fix.",
};

export const OUTCOME: Record<string, string> = {
  SUBMIT: "Submit\nLow rejection risk: send the claim as it is.",
  HOLD_REFER_TO_SIU: "Hold: refer to investigation\nThe claim looks like fraud, so it goes to the insurer's Special Investigation Unit. No appeal is written.",
  FIX_AND_RESUBMIT: "Fix and resubmit\nThe claim really breaks a policy rule (e.g. missing documents), so fix it rather than appeal.",
  APPEAL_STRONG: "Appeal: strong\nThe claim follows the rules but is likely to be (or was) rejected. An appeal with solid citations was drafted.",
  APPEAL_NEEDS_REVIEW: "Appeal: needs review\nAn appeal was drafted, but its self-check found issues. Read it before sending.",
  APPEAL_WEAK: "Appeal: weak\nThe insurer's reason seems valid, so an appeal is unlikely to succeed.",
};

export const STEP: Record<string, string> = {
  fraud_screen: "Fraud check: the fraud model scores the claim. 70% or more stops the pipeline.",
  refer_siu: "Sent to the investigation unit because the claim looks fraudulent.",
  draft_preauth: "Pre-approval was required but missing, so the pre-auth agent drafted one.",
  denial_predict: "Rejection prediction: the model estimates the risk, and the rulebook checks whether the claim breaks any rule.",
  submit: "Low risk: submit as is.",
  fix_and_resubmit: "The claim breaks a real rule, so fix it.",
  appeal: "The claim is valid but at risk, so the appeal agent wrote a letter.",
};

// ---------------------------------------------------------------- denial page
export const DENIAL = {
  probability: "The model's estimate that the insurer will reject this claim (0–100%).",
  band: "LOW below 30% · MEDIUM 30–60% · HIGH 60%+.",
  version: "Exact version of the model that made this prediction. It is stored in the audit log too.",
  reasons: "The most likely rejection reasons = chance of rejection × chance of each reason. Hover a code for its meaning.",
  drivers: "SHAP explanation: how much each fact about the claim pushed the prediction up (red, towards rejection) or down (green).",
  feature: "A fact about the claim that the model looks at.",
  value: "This claim's value for that fact (1 = yes, 0 = no for yes/no facts).",
  contribution: "Bar length = how strongly this fact moved the prediction. Red = towards rejection, green = away from it.",
  logodds: "The same contribution as a number (in log-odds, the model's internal scale). Bigger = stronger push.",
  hints: "Which rejection code this fact relates to. Hover the code for its meaning.",
  coverSince: "Date the patient's insurance policy started.",
  dxFirstSeen: "Date this illness was FIRST diagnosed. If it is after 'Cover since', the illness is NOT pre-existing.",
  insurerSaid: "The insurer's actual (simulated) decision on this claim.",
};

// ---------------------------------------------------------------- appeal page
export const APPEAL = {
  strength: "How solid the appeal is, judged by code (not by the AI).",
  denialTag: "The rejection reason being appealed. 'actual' = the insurer really rejected it; 'predicted' = our model expects a rejection.",
  generatedBy: "Which AI wrote this letter. 'template' means no AI was used (offline mode).",
  grounds: "The core argument, worked out by the rulebook in code (not by the AI), e.g. the illness was first found after the policy began.",
  sources: "The exact policy and clinical text the letter quotes. Each tag in the letter links here.",
  trace: "What the AI agent did behind the scenes.",
  rounds: "How many times the agent searched the documents. It searches again if the first results are weak.",
  revisions: "How many times the agent rewrote the letter after its citation check failed.",
  issues: "Problems the final check still found (e.g. a paragraph without a source).",
  queries: "The search questions the AI wrote for itself to find supporting rules.",
  cite: "A quote from the source documents. Hover to read the quoted text; click to jump to it.",
};
export const STRENGTH: Record<string, string> = {
  STRONG: "Strong: the claim follows the policy, and every argument is backed by a quoted rule.",
  NEEDS_REVIEW: "Needs review: the draft has issues from its self-check. Read it before sending.",
  WEAK: "Weak: the insurer's reason appears valid under the policy. Fixing the claim is better than appealing.",
};

// ---------------------------------------------------------------- pre-auth page
export const PREAUTH = {
  patient: "Pick a patient. The list is sorted by number of hospital stays, since pre-approval matters most for admissions.",
  encounter: "Pick which visit to request approval for, or leave it on automatic (latest hospital stay).",
  fhir: "Copied straight from the patient's FHIR medical record (the standard format for health data). No AI involved.",
  rules: "Checked by the plan's rulebook in code. No AI involved, so these facts are exact.",
  covered: "Whether the plan pays for this treatment.",
  preauthRequired: "Whether the insurer must approve this BEFORE treatment.",
  category: "The treatment category used for plan rules (cardiac, orthopaedic, …).",
  sublimit: "The plan's maximum payout for this category.",
  ped: "Pre-existing disease status: was this illness present before the policy started? If so, a waiting period may apply.",
  justification: "Written by the AI from the retrieved policy rules and clinical notes.",
  required: "What the insurer's policy says this request must include.",
  necessity: "A draft statement of why the treatment is medically needed.",
  missing: "Documents or values the medical record doesn't have but the insurer will want.",
  citations: "The policy and clinical text the AI relied on.",
  fhirClaim: "The finished request as a FHIR 'Claim' with use = preauthorization: the format real systems exchange.",
  draft: "DRAFT: a person must review and submit it. The system never submits on its own.",
};

// ---------------------------------------------------------------- fraud page
export const FRAUD = {
  backend: "Where the community detection ran: neo4j-gds = inside the Neo4j database; networkx = in Python.",
  threshold: "A group is flagged as a ring when its ring score is at or above this value.",
  eval: "Checked against the answer key of the fraud rings we planted. Precision 1 = no innocent people flagged; recall 1 = every ring member found.",
  score: "Ring score (0–1) = average of this group's two strongest fraud patterns.",
  billed: "Total amount claimed by everyone in this group.",
  overlap: "How many patients in this group belong to each planted ring. Shown only because this is a learning project.",
  cypher: "Counts from the hand-written graph queries (graph/fraud_queries.cypher) run over the whole database.",
};
export const COMPONENT: Record<string, string> = {
  identity: "Many 'different' patients use the same phone number or home address: a sign of fake patients.",
  velocity: "Patients filing 5+ claims within 90 days: unusually frequent billing.",
  doc_mesh: "The same doctors billing at 3+ clinics in the group: one operation hiding behind several names.",
  referral_loop: "Doctors referring patients in a closed circle (A→B→C→A) to generate extra visits: a kickback sign.",
  structuring: "Bills kept just under the pre-approval limit (90–99%) so no insurer reviewer checks them.",
};
export const NODE_KIND: Record<string, string> = {
  Patient: "Patient: a person who filed claims.",
  Organization: "Clinic or hospital.",
  Practitioner: "Doctor.",
  Address: "A home address shown because 2+ patients in the group share it.",
  Phone: "A phone number shown because 2+ patients in the group share it.",
};

// ---------------------------------------------------------------- audit page
export const AUDIT_COL: Record<string, string> = {
  "#": "Record number, in the order decisions were made.",
  "Time (UTC)": "When the decision was made (UTC; India is UTC+5:30).",
  Module: "Which part of the system decided. Hover the badge for details.",
  Subject: "What the decision was about: a claim or a patient (short id).",
  Recommendation: "What the system decided. Hover it for its meaning.",
  "Model version": "Exact model (or rule set) that made the decision, so it can be traced later.",
  LLM: "Which AI wrote text for this decision. '—' = no AI involved; template/none = offline template.",
};
export const AUDIT = {
  chain: "Every record stores a fingerprint (hash) of the previous one. Editing or deleting any old record breaks the chain, and this banner turns red.",
  module: "Filter records by system part.",
  subject: "Paste a claim or patient id to see only its decisions.",
};
export const MODULE: Record<string, string> = {
  orchestrator: "Orchestrator: the 'manager' that routed the claim through the steps.",
  fraud: "Fraud model: scored how likely the claim is fraud.",
  denial: "Denial model: predicted rejection risk and reasons.",
  preauth: "Pre-auth agent: drafted a pre-approval request.",
  appeal: "Appeal agent: drafted an appeal letter.",
};
export const RECOMMENDATION: Record<string, string> = {
  ...OUTCOME,
  HIGH_RISK: "High risk: 60%+ chance of rejection.",
  MEDIUM_RISK: "Medium risk: 30–60% chance of rejection.",
  LOW_RISK: "Low risk: under 30% chance of rejection.",
  CLEAR: "Clear: the fraud score is below the 70% alarm level.",
  FLAG_FRAUD: "Flagged as fraud: the fraud score is 70% or more.",
  SUBMIT_PREAUTH: "Pre-approval is required: submit the drafted request.",
  PREAUTH_NOT_REQUIRED: "This treatment doesn't need pre-approval.",
  LIKELY_NOT_COVERED: "The plan probably won't pay for this treatment.",
  NO_COVERAGE: "No insurance policy was found for this patient.",
};
export const recommendationTip = (code: string) => lookup(RECOMMENDATION, code);
