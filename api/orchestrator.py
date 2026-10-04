"""
api/orchestrator.py — the master LangGraph that ROUTES a claim between modules.

    python -m api.orchestrator <claim_id>

  START
    │
    ▼
  fraud_screen ──(fraud ≥ 0.7)──────────────────────────────▶ refer_siu ──┐
    │ (clear)                                                             │
    ▼                                                                     │
  preauth_gate ──(pre-auth required but missing)──▶ draft_preauth ──┐     │
    │ (ok)                                                          │     │
    ▼◀──────────────────────────────────────────────────────────────┘     │
  denial_predict                                                          │
    │                                                                     │
    ├─(low risk & not denied)────────────────────────▶ submit ────────────┤
    ├─(denied/high risk, rules genuinely violated)───▶ fix_and_resubmit ──┤
    └─(denied/high risk, claim is policy-compliant)──▶ appeal ────────────┤
                                                                          ▼
                                                                       finalize → END

WHY route this way?
  * Fraud first: you never want to auto-appeal (i.e. argue FOR paying) a claim
    that belongs to a fraud ring. That would be the system helping the fraud.
  * Pre-auth before denial prediction: a missing pre-auth is the cheapest
    denial to prevent, and the pre-auth agent can draft it right now.
  * Appeal only when the deterministic rulebook says the claim is compliant.
    "High denial risk + compliant claim" is exactly the definition of a likely
    WRONGFUL denial. If a rule is genuinely violated, an appeal would be
    frivolous; the right action is to fix the claim (add documents, etc.).
Every step writes its own audit record; `finalize` writes the routing decision.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from functools import lru_cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from appeal.agent import run_appeal
from brain.predict import get_brain
from data.reference import evaluate_policy
from data.simulate_adjudication import build_claim_context
from preauth.agent import run_preauth
from trust.audit import record_decision

FRAUD_THRESHOLD = 0.7
DENIAL_THRESHOLD = 0.5


class ClaimState(TypedDict, total=False):
    claim_id: str
    claim: dict
    fraud: dict
    preauth: dict | None
    denial: dict
    violations: list[dict]
    outcome: str
    steps: list[dict]          # human-readable trail for the UI
    appeal: dict | None
    decision_id: str


def _step(state: ClaimState, name: str, summary: str, decision_id: str | None = None) -> list[dict]:
    return state.get("steps", []) + [{"step": name, "summary": summary, "decision_id": decision_id}]


def fraud_screen(state: ClaimState) -> dict:
    brain = get_brain()
    claim = brain.claim_row(state["claim_id"])
    f = brain.fraud(state["claim_id"])
    rec = "FLAG_FRAUD" if f["fraud_score"] >= FRAUD_THRESHOLD else "CLEAR"
    did = record_decision(module="fraud", subject_id=state["claim_id"], recommendation=rec,
                          inputs={"features": {d["feature"]: d["value"] for d in f["drivers"]}},
                          output=f, model_name="fraud_model", model_version=f["model_version"])
    return {"claim": claim, "fraud": f,
            "steps": _step(state, "fraud_screen", f"fraud score {f['fraud_score']:.2f} → {rec}", did)}


def route_fraud(state: ClaimState) -> str:
    return "refer_siu" if state["fraud"]["fraud_score"] >= FRAUD_THRESHOLD else "preauth_gate"


def refer_siu(state: ClaimState) -> dict:
    return {"outcome": "HOLD_REFER_TO_SIU",
            "steps": _step(state, "refer_siu", "Held for the Special Investigation Unit; no appeal will be drafted.")}


def preauth_gate(state: ClaimState) -> dict:
    return {}  # pure routing node; the decision lives in route_preauth


def route_preauth(state: ClaimState) -> str:
    c = state["claim"]
    return "draft_preauth" if c["requires_preauth"] and not c["preauth_obtained"] else "denial_predict"


def draft_preauth(state: ClaimState) -> dict:
    c = state["claim"]
    req = run_preauth(c["patient_id"], c["encounter_id"])
    return {"preauth": req, "steps": _step(state, "draft_preauth",
                                           f"Pre-auth required but missing → drafted {req['request_id']}",
                                           req.get("decision_id"))}


def denial_predict(state: ClaimState) -> dict:
    brain = get_brain()
    d = brain.denial(state["claim_id"])
    c = state["claim"]
    ctx = build_claim_context(c, date.fromisoformat(str(c["coverage_start"])[:10]))
    if state.get("preauth"):
        ctx["preauth_obtained"] = True  # we just drafted it; assume it will be submitted
    violations = evaluate_policy(ctx)
    rec = f"{d['risk_band']}_RISK"
    did = record_decision(module="denial", subject_id=state["claim_id"], recommendation=rec,
                          inputs={"features": {x["feature"]: x["value"] for x in d["drivers"]}},
                          output={**d, "rule_violations": violations}, model_name="denial_model",
                          model_version=d["model_version"])
    top = d["top_reasons"][0]
    return {"denial": d, "violations": violations,
            "steps": _step(state, "denial_predict",
                           f"denial risk {d['denial_risk']:.2f} (top reason {top['code']} {top['label']}); "
                           f"rule violations: {[v['code'] for v in violations] or 'none'}", did)}


def route_denial(state: ClaimState) -> str:
    d, c = state["denial"], state["claim"]
    if not c["denied"] and d["denial_risk"] < DENIAL_THRESHOLD:
        return "submit"
    return "fix_and_resubmit" if state["violations"] else "appeal"


def submit(state: ClaimState) -> dict:
    return {"outcome": "SUBMIT", "steps": _step(state, "submit", "Low denial risk. Submit as is.")}


def fix_and_resubmit(state: ClaimState) -> dict:
    fixes = "; ".join(v["detail"] for v in state["violations"])
    return {"outcome": "FIX_AND_RESUBMIT", "steps": _step(state, "fix_and_resubmit", f"Genuine policy issues: {fixes}")}


def appeal(state: ClaimState) -> dict:
    a = run_appeal(state["claim_id"])
    return {"appeal": a, "outcome": f"APPEAL_{a['appeal_strength']}",
            "steps": _step(state, "appeal", f"Claim is policy-compliant; appeal drafted ({a['appeal_strength']})",
                           a["decision_id"])}


def finalize(state: ClaimState) -> dict:
    did = record_decision(
        module="orchestrator", subject_id=state["claim_id"], recommendation=state["outcome"],
        inputs={"fraud_score": state["fraud"]["fraud_score"],
                "denial_risk": state.get("denial", {}).get("denial_risk"),
                "violations": [v["code"] for v in state.get("violations", [])]},
        output={"steps": state["steps"]}, model_name="orchestrator",
        model_version=f"fraud>={FRAUD_THRESHOLD};denial>={DENIAL_THRESHOLD}")
    return {"decision_id": did}


@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(ClaimState)
    for n, fn in [("fraud_screen", fraud_screen), ("refer_siu", refer_siu), ("preauth_gate", preauth_gate),
                  ("draft_preauth", draft_preauth), ("denial_predict", denial_predict), ("submit", submit),
                  ("fix_and_resubmit", fix_and_resubmit), ("appeal", appeal), ("finalize", finalize)]:
        g.add_node(n, fn)
    g.add_edge(START, "fraud_screen")
    g.add_conditional_edges("fraud_screen", route_fraud, {"refer_siu": "refer_siu", "preauth_gate": "preauth_gate"})
    g.add_conditional_edges("preauth_gate", route_preauth,
                            {"draft_preauth": "draft_preauth", "denial_predict": "denial_predict"})
    g.add_edge("draft_preauth", "denial_predict")
    g.add_conditional_edges("denial_predict", route_denial,
                            {"submit": "submit", "fix_and_resubmit": "fix_and_resubmit", "appeal": "appeal"})
    for terminal in ("refer_siu", "submit", "fix_and_resubmit", "appeal"):
        g.add_edge(terminal, "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def process_claim(claim_id: str) -> dict:
    s = build_graph().invoke({"claim_id": claim_id, "steps": []})
    return {"claim_id": claim_id, "outcome": s["outcome"], "steps": s["steps"], "decision_id": s["decision_id"],
            "fraud": s["fraud"], "denial": s.get("denial"), "violations": s.get("violations", []),
            "preauth": s.get("preauth"), "appeal": s.get("appeal")}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m api.orchestrator <claim_id>")
    r = process_claim(sys.argv[1])
    print(json.dumps({k: r[k] for k in ("outcome", "steps", "decision_id")}, indent=2))
