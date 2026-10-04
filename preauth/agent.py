"""
preauth/agent.py — LangGraph agent that drafts a pre-authorisation request.

    python -m preauth.agent <patient_id> [encounter_id]

GRAPH
                 ┌────────────┐
       START ──▶ │ load_fhir  │  read the patient's FHIR bundle
                 └─────┬──────┘
                       ▼
                 ┌────────────┐
                 │ map_fields │  FHIR -> form fields (deterministic, no LLM)
                 └─────┬──────┘
                       ▼
                 ┌────────────┐
                 │ check_rules│  rule engine: covered? pre-auth needed? PED? sub-limit?
                 └─────┬──────┘
          coverage? ───┤
          no ──────────┼──────────────────────────────┐
          yes          ▼                              │
                 ┌────────────┐                       │
                 │ retrieve   │  RAG: policy clauses + clinical guidance
                 └─────┬──────┘                       │
                       ▼                              │
                 ┌────────────┐                       │
                 │ justify    │  LLM: required justification + necessity draft
                 └─────┬──────┘                       │
                       ▼                              ▼
                 ┌────────────┐
                 │ assemble   │  build PreAuthRequest, list missing info
                 └─────┬──────┘
                       ▼
                 ┌────────────┐
                 │ audit      │  write the decision record
                 └─────┬──────┘
                       ▼
                      END

WHY LangGraph instead of one big function or one big prompt?
  * Each node does ONE thing and writes its result into a shared, typed STATE.
    You can inspect the state after any node, which makes debugging and
    teaching much easier.
  * Facts come from deterministic nodes (map_fields, check_rules). Only
    `justify` uses the LLM, so the LLM cannot invent a policy number or an amount.
  * Conditional edges (`route_after_rules`) make the control flow explicit. With
    no coverage on file there is nothing to justify, so we skip RAG and the LLM.
  * The same pattern scales to the appeal agent, which loops.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from core.llm import get_llm
from core.rag import retrieve
from data.reference import PLANS, category_for, evaluate_policy, is_chronic, requires_preauth
from preauth import fhir_reader
from preauth.schema import (Citation, CoverageCheck, CoverageInfo, Diagnosis, EncounterInfo, PatientInfo,
                            PreAuthRequest)
from trust.audit import record_decision


class PreAuthState(TypedDict, total=False):
    """The shared state. Every node returns a partial dict that LangGraph
    merges into this. `total=False` = nodes fill keys progressively."""
    patient_id: str
    encounter_id: str | None
    raw: dict                    # output of fhir_reader.extract
    patient: dict
    coverage: dict | None
    encounter: dict
    diagnoses: list[dict]
    service: dict
    check: dict | None
    policy_chunks: list[dict]
    clinical_chunks: list[dict]
    justification: dict
    request: dict
    decision_id: str


# ---------------------------------------------------------------- nodes
def load_fhir(state: PreAuthState) -> dict:
    return {"raw": fhir_reader.extract(state["patient_id"], state.get("encounter_id"))}


def map_fields(state: PreAuthState) -> dict:
    raw = state["raw"]
    enc = raw["encounter"]
    patient = {**raw["patient"], "age": fhir_reader.age_on(raw["patient"]["birth_date"], enc["start"] or str(date.today()))}

    cov_src = raw["coverage_fhir"] or fhir_reader.coverage_from_tables(state["patient_id"])
    coverage = None
    if cov_src and cov_src.get("plan_code") in PLANS:
        plan = PLANS[cov_src["plan_code"]]
        coverage = {"coverage_id": cov_src["coverage_id"], "policy_number": cov_src.get("policy_number"),
                    "plan_code": plan.code, "plan_name": plan.name, "insurer": plan.insurer,
                    "policy_start": str(cov_src["start"])[:10], "sum_insured_inr": plan.sum_insured_inr,
                    "synthesized": bool(cov_src.get("synthesized", False))}

    hint = raw["claim_hint"] or {}
    primary = next((d for d in raw["diagnoses"] if d["is_primary"]), None)
    service = {"name": hint.get("service") or enc["type"] or "Consultation",
               "code": hint.get("service_code"),
               "estimated_cost_inr": float(hint.get("amount_inr") or 0.0),
               "primary_dx": primary}
    return {"patient": patient, "coverage": coverage, "encounter": enc,
            "diagnoses": raw["diagnoses"], "service": service}


def check_rules(state: PreAuthState) -> dict:
    cov, enc, svc = state["coverage"], state["encounter"], state["service"]
    if cov is None:
        return {"check": None}
    plan = PLANS[cov["plan_code"]]
    dx = svc["primary_dx"] or {}
    cat = category_for(dx.get("display"))
    ctx = {
        "plan_code": plan.code, "category": cat, "encounter_class": enc["encounter_class"],
        "total_inr": svc["estimated_cost_inr"],
        "preauth_obtained": True,          # we ARE the pre-auth: don't flag D01 against ourselves
        "chronic": is_chronic(dx.get("display")),
        "dx_onset": date.fromisoformat(dx["onset"]) if dx.get("onset") else None,
        "coverage_start": date.fromisoformat(cov["policy_start"]),
        "claim_date": date.fromisoformat(enc["start"]) if enc["start"] else date.today(),
        "docs_complete": True, "days_to_submit": 0,
    }
    violations = evaluate_policy(ctx)
    ped = next((v for v in violations if v["code"] == "D03"), None)
    if ped:
        ped_status = "WAITING_PERIOD_APPLIES: " + ped["detail"]
    elif ctx["chronic"] and ctx["dx_onset"] and ctx["dx_onset"] >= ctx["coverage_start"]:
        ped_status = "NOT_PED: chronic condition first diagnosed after policy start"
    elif ctx["chronic"]:
        ped_status = "PED_WAITING_COMPLETED_OR_NOT_APPLICABLE"
    else:
        ped_status = "NOT_APPLICABLE: acute condition"
    check = {"covered": not any(v["code"] in ("D02", "D03") for v in violations),
             "preauth_required": requires_preauth(plan, enc["encounter_class"], svc["estimated_cost_inr"]),
             "category": cat, "sublimit_inr": plan.sublimits_inr.get(cat),
             "ped_status": ped_status, "violations": violations}
    return {"check": check}


def route_after_rules(state: PreAuthState) -> str:
    """Conditional edge: no coverage -> nothing to justify against."""
    return "retrieve" if state.get("coverage") else "assemble"


def retrieve_context(state: PreAuthState) -> dict:
    cov, chk, svc = state["coverage"], state["check"], state["service"]
    dx = (svc["primary_dx"] or {}).get("display", "")
    # The query mixes the procedure, the diagnosis and the rule topics in play,
    # so the retriever surfaces the pre-auth clause AND any sub-limit/PED clause.
    topics = ["pre-authorisation requirements contents of request", svc["name"], dx]
    if chk and chk["sublimit_inr"]:
        topics.append(f"{chk['category']} sub-limit")
    if chk and "PED" in chk["ped_status"]:
        topics.append("pre-existing disease definition waiting period")
    policy = retrieve(" ; ".join(topics), "policies", k=5, plan_code=cov["plan_code"])
    clinical = retrieve(f"{dx} {svc['name']} medical necessity documentation", "clinical", k=3)
    return {"policy_chunks": [c.as_dict() for c in policy], "clinical_chunks": [c.as_dict() for c in clinical]}


SYSTEM_JUSTIFY = """You are a pre-authorisation specialist at an Indian hospital's insurance desk.
You draft the justification section of a cashless pre-authorisation request.
Use ONLY the facts and source excerpts provided. Never invent test results, values or dates.
When a needed fact is not in the record, list it under missing_information instead of guessing."""


def justify(state: PreAuthState) -> dict:
    llm = get_llm()
    svc, chk = state["service"], state["check"]
    sources = state["policy_chunks"] + state["clinical_chunks"]
    src_txt = "\n\n".join(f"[{c['chunk_id']}] ({c['section']})\n{c['text']}" for c in sources)
    # Name the code system explicitly; otherwise models tend to call SNOMED codes "ICD".
    diagnoses = [{**d, "code_system": "SNOMED CT"} for d in state["diagnoses"]]
    facts = {"patient": state["patient"], "encounter": state["encounter"], "diagnoses": diagnoses,
             "requested_service": svc["name"], "estimated_cost_inr": svc["estimated_cost_inr"],
             "rule_engine": chk}
    user = f"""FACTS (from FHIR record and rule engine):
{json.dumps(facts, indent=2, default=str)}

SOURCES:
{src_txt}

Return JSON with keys:
  "required_justification": list of short items the insurer's policy requires for this request (cite chunk ids in brackets),
  "clinical_justification": 3-5 sentence medical-necessity statement grounded in the clinical sources,
  "missing_information": list of documents/values needed but absent from the facts,
  "citations": list of chunk ids you relied on."""
    default = _template_justification(state)
    out = llm.complete_json(SYSTEM_JUSTIFY, user, default=default)
    # Keep only citations that were actually provided. A hallucinated id is dropped.
    valid = {c["chunk_id"] for c in sources}
    cites = out.get("citations") if isinstance(out.get("citations"), list) else []
    out["citations"] = [c for c in cites if isinstance(c, str) and c in valid] or default["citations"]
    for k in ("required_justification", "missing_information"):
        if not isinstance(out.get(k), list):
            out[k] = default[k]
        out[k] = [x if isinstance(x, str) else json.dumps(x) for x in out[k]]
    if not isinstance(out.get("clinical_justification"), str) or not out["clinical_justification"].strip():
        out["clinical_justification"] = default["clinical_justification"]
    out["generated_by"] = "template" if llm.is_mock or out is default else f"{llm.provider}/{llm.model_name}"
    return {"justification": out}


def _template_justification(state: PreAuthState) -> dict:
    """Deterministic fallback (mock provider or LLM failure). Boring but safe."""
    svc, chk, enc = state["service"], state.get("check") or {}, state["encounter"]
    dx = (svc["primary_dx"] or {}).get("display", "the documented condition")
    pol = state.get("policy_chunks", [])
    req = ["Diagnosis with SNOMED/ICD code", "Proposed line of treatment and estimated cost",
           "Treating doctor's statement of medical necessity", "Supporting investigation reports"]
    if enc["encounter_class"] == "IMP":
        req.append("Expected length of stay")
    if chk.get("sublimit_inr"):
        req.append(f"Note: {chk['category']} sub-limit ₹{chk['sublimit_inr']:,} applies")
    return {
        "required_justification": req,
        "clinical_justification": (f"The patient presented to {enc['provider_org']} with {dx}. "
                                   f"{svc['name']} is requested as the indicated treatment per the attached clinical "
                                   f"guidance. Estimated cost is ₹{svc['estimated_cost_inr']:,.0f}. "
                                   "The treating doctor will attach investigation reports supporting medical necessity."),
        "missing_information": ["Investigation reports (not present in FHIR record)",
                                "Doctor's signed treatment plan"],
        "citations": [c["chunk_id"] for c in pol[:2]] + [c["chunk_id"] for c in state.get("clinical_chunks", [])[:1]],
    }


def assemble(state: PreAuthState) -> dict:
    j = state.get("justification") or {}
    svc = state["service"]
    sources = {c["chunk_id"]: c for c in state.get("policy_chunks", []) + state.get("clinical_chunks", [])}
    missing = list(j.get("missing_information", []))
    if state.get("coverage") is None:
        missing.insert(0, "No active coverage found for patient. Cannot raise pre-authorisation.")
    if not state["diagnoses"]:
        missing.insert(0, "No diagnosis recorded for this encounter")
    req = PreAuthRequest(
        request_id=f"PA-{uuid.uuid4().hex[:10].upper()}",
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        patient=PatientInfo(**state["patient"]),
        coverage=CoverageInfo(**state["coverage"]) if state.get("coverage") else None,
        encounter=EncounterInfo(**state["encounter"]),
        diagnoses=[Diagnosis(code=d["code"], display=d["display"], onset=d["onset"], is_primary=d["is_primary"])
                   for d in state["diagnoses"]],
        requested_service=svc["name"], requested_service_code=svc["code"],
        estimated_cost_inr=svc["estimated_cost_inr"],
        coverage_check=CoverageCheck(**state["check"]) if state.get("check") else None,
        required_justification=j.get("required_justification", []),
        clinical_justification=j.get("clinical_justification", ""),
        missing_information=missing,
        citations=[Citation(chunk_id=cid, source=sources[cid]["source"], section=sources[cid]["section"],
                            quote=sources[cid]["text"][:300]) for cid in j.get("citations", []) if cid in sources],
        generated_by=j.get("generated_by", "n/a"),
    )
    return {"request": req.model_dump()}


def audit(state: PreAuthState) -> dict:
    req, chk = state["request"], state.get("check")
    if req["coverage"] is None:
        rec = "NO_COVERAGE"
    elif chk and not chk["covered"]:
        rec = "LIKELY_NOT_COVERED"
    elif chk and chk["preauth_required"]:
        rec = "SUBMIT_PREAUTH"
    else:
        rec = "PREAUTH_NOT_REQUIRED"
    llm = get_llm()
    did = record_decision(
        module="preauth", subject_type="patient", subject_id=state["patient_id"], recommendation=rec,
        inputs={"encounter": state["encounter"], "diagnoses": state["diagnoses"], "service": state["service"],
                "coverage": state.get("coverage")},
        output={"request_id": req["request_id"], "coverage_check": chk,
                "required_justification": req["required_justification"],
                "clinical_justification": req["clinical_justification"],
                "missing_information": req["missing_information"]},
        model_name="rule-engine+llm", model_version="data/reference.py",
        llm=llm.describe() if req["generated_by"] != "template" else {"llm_provider": "template", "llm_model": "none"},
        retrieval=[{"chunk_id": c["chunk_id"], "source": c["source"], "score": c["score"]}
                   for c in state.get("policy_chunks", []) + state.get("clinical_chunks", [])],
    )
    req["decision_id"] = did
    return {"request": req, "decision_id": did}


# ---------------------------------------------------------------- graph
@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(PreAuthState)
    g.add_node("load_fhir", load_fhir)
    g.add_node("map_fields", map_fields)
    g.add_node("check_rules", check_rules)
    g.add_node("retrieve", retrieve_context)
    g.add_node("justify", justify)
    g.add_node("assemble", assemble)
    g.add_node("audit", audit)
    g.add_edge(START, "load_fhir")
    g.add_edge("load_fhir", "map_fields")
    g.add_edge("map_fields", "check_rules")
    g.add_conditional_edges("check_rules", route_after_rules, {"retrieve": "retrieve", "assemble": "assemble"})
    g.add_edge("retrieve", "justify")
    g.add_edge("justify", "assemble")
    g.add_edge("assemble", "audit")
    g.add_edge("audit", END)
    return g.compile()


def run_preauth(patient_id: str, encounter_id: str | None = None) -> dict[str, Any]:
    final = build_graph().invoke({"patient_id": patient_id, "encounter_id": encounter_id})
    req = final["request"]
    req["fhir_claim"] = PreAuthRequest(**{k: v for k, v in req.items() if k != "fhir_claim"}).to_fhir_claim()
    return req


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m preauth.agent <patient_id> [encounter_id]")
    print(json.dumps(run_preauth(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None), indent=2, default=str))
