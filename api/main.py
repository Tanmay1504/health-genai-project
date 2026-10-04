"""
api/main.py — FastAPI backend: the single door the Next.js UI knocks on.

    uvicorn api.main:app --reload --port 8000
    open http://localhost:8000/docs        (interactive Swagger UI)

WHY a separate API layer?
  The UI should not know about XGBoost, LangGraph or SQLite. This file only
  translates HTTP <-> Python calls into the modules, so each module stays
  testable on its own and the UI could be replaced without touching them.

Endpoints (all JSON):
  GET  /api/health                       providers, Neo4j status, data counts
  GET  /api/claims                       list with denial risk + fraud score
  GET  /api/claims/{id}                  claim detail
  GET  /api/patients                     patients (for the pre-auth picker)
  GET  /api/patients/{id}/encounters
  POST /api/preauth                      {patient_id, encounter_id?} → draft
  GET  /api/fraud/rings                  ring candidates + subgraphs
  GET  /api/denial/{claim_id}            risk, reasons, SHAP drivers (audited)
  POST /api/appeal                       {claim_id} → appeal letter (audited)
  POST /api/process/{claim_id}           full LangGraph routing (audited)
  GET  /api/audit                        decision log (filters: module, subject_id)
  GET  /api/audit/verify                 hash-chain integrity
  GET  /api/audit/{decision_id}
  GET  /api/models                       model versions + metrics
"""
from __future__ import annotations

import json
import logging
import math

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from brain.predict import get_brain
from core.config import PATHS, settings
from trust import audit

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = FastAPI(title="HEALTH_GEN_AI API", version="1.0.0",
              description="Pre-auth, fraud rings, denial prediction, auto-appeal and audit (synthetic data only).")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                   allow_methods=["*"], allow_headers=["*"])


def _clean(v):
    """Make pandas/numpy values JSON-safe (NaN -> None, numpy -> python)."""
    if isinstance(v, float) and math.isnan(v):
        return None
    return v.item() if hasattr(v, "item") else v


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    # Turn infrastructure failures (Ollama down, Neo4j down, ...) into a readable
    # JSON error the UI can display, instead of a bare "Internal Server Error".
    from fastapi.responses import JSONResponse
    logging.getLogger("api").exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=503, content={"detail": f"{exc.__class__.__name__}: {exc}"})


def _brain():
    try:
        return get_brain()
    except RuntimeError as e:
        raise HTTPException(503, str(e))


# --------------------------------------------------------------------------- health
@app.get("/api/health")
def health():
    from graph.db import neo4j_available  # local import: driver creation is lazy
    counts = {}
    for t in ("patients", "claims_labeled", "graph_features"):
        f = PATHS.processed / f"{t}.csv"
        counts[t] = sum(1 for _ in f.open()) - 1 if f.exists() else 0
    return {"status": "ok", "llm_provider": settings.llm_provider,
            "neo4j": neo4j_available(), "models_trained": (PATHS.models / "metadata.json").exists(),
            "rows": counts}


@app.get("/api/models")
def models():
    return _brain().meta


# --------------------------------------------------------------------------- claims
CLAIM_LIST_COLS = ["claim_id", "patient_id", "name", "created", "total_inr", "encounter_class", "category",
                   "dx_display", "plan_code", "denied", "denial_reason", "denial_wrongful"]


@app.get("/api/claims")
def list_claims(view: str = Query("all", pattern="^(all|high_risk|fraud|wrongful|denied)$"),
                q: str = "", limit: int = Query(100, le=1000)):
    b = _brain()
    df = b.df[CLAIM_LIST_COLS].join(b.scores())
    if view == "high_risk":
        df = df[df.denial_risk >= 0.5]
    elif view == "fraud":
        df = df[df.fraud_score >= 0.5]
    elif view == "wrongful":
        df = df[df.denial_wrongful == 1]  # synthetic ground truth: shown for learning
    elif view == "denied":
        df = df[df.denied == 1]
    if q:
        ql = q.lower()
        df = df[df.claim_id.str.contains(ql) | df.name.str.lower().str.contains(ql) |
                df.dx_display.fillna("").str.lower().str.contains(ql)]
    total = int(len(df))
    df = df.sort_values(["fraud_score" if view == "fraud" else "denial_risk"], ascending=False).head(limit)
    return {"total": total, "claims": [{k: _clean(v) for k, v in r.items()} for r in df.to_dict("records")]}


@app.get("/api/claims/{claim_id}")
def claim_detail(claim_id: str):
    b = _brain()
    try:
        return {"claim": b.claim_row(claim_id), "scores": b.scores().loc[claim_id].to_dict()}
    except KeyError:
        raise HTTPException(404, "claim not found")


# --------------------------------------------------------------------------- patients
@app.get("/api/patients")
def list_patients(q: str = "", limit: int = Query(50, le=500)):
    pats = pd.read_csv(PATHS.processed / "patients.csv")
    enc = pd.read_csv(PATHS.processed / "encounters.csv")
    n_imp = enc[enc.encounter_class == "IMP"].groupby("patient_id").size()
    pats["inpatient_stays"] = pats.patient_id.map(n_imp).fillna(0).astype(int)
    if q:
        pats = pats[pats.name.str.lower().str.contains(q.lower()) | pats.patient_id.str.contains(q)]
    pats = pats.sort_values("inpatient_stays", ascending=False).head(limit)
    cols = ["patient_id", "name", "gender", "birth_date", "city", "inpatient_stays"]
    return [{k: _clean(v) for k, v in r.items()} for r in pats[cols].to_dict("records")]


@app.get("/api/patients/{patient_id}/encounters")
def patient_encounters(patient_id: str):
    enc = pd.read_csv(PATHS.processed / "encounters.csv")
    e = enc[enc.patient_id == patient_id].sort_values("start", ascending=False)
    return [{k: _clean(v) for k, v in r.items()} for r in e.to_dict("records")]


# --------------------------------------------------------------------------- pre-auth
class PreAuthIn(BaseModel):
    patient_id: str
    encounter_id: str | None = None


@app.post("/api/preauth")
def preauth(body: PreAuthIn):
    from preauth.agent import run_preauth
    try:
        return run_preauth(body.patient_id, body.encounter_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


# --------------------------------------------------------------------------- fraud
@app.get("/api/fraud/rings")
def fraud_rings():
    f = PATHS.processed / "fraud_rings.json"
    if not f.exists():
        raise HTTPException(503, "Run `python -m graph.graph_analytics` first.")
    return json.loads(f.read_text())


@app.get("/api/fraud/claim/{claim_id}")
def fraud_claim(claim_id: str):
    try:
        f = _brain().fraud(claim_id)
    except KeyError:
        raise HTTPException(404, "claim not found")
    f["decision_id"] = audit.record_decision(
        module="fraud", subject_id=claim_id, recommendation="FLAG_FRAUD" if f["fraud_score"] >= 0.7 else "CLEAR",
        inputs={"features": {d["feature"]: d["value"] for d in f["drivers"]}}, output=f,
        model_name="fraud_model", model_version=f["model_version"])
    return f


# --------------------------------------------------------------------------- denial
@app.get("/api/denial/{claim_id}")
def denial(claim_id: str):
    b = _brain()
    try:
        d = b.denial(claim_id)
    except KeyError:
        raise HTTPException(404, "claim not found")
    d["claim"] = {k: _clean(v) for k, v in b.claim_row(claim_id).items()
                  if k in CLAIM_LIST_COLS + ["procedure", "coverage_start", "dx_onset", "preauth_obtained",
                                             "requires_preauth", "docs_complete", "days_to_submit"]}
    d["decision_id"] = audit.record_decision(
        module="denial", subject_id=claim_id, recommendation=f"{d['risk_band']}_RISK",
        inputs={"features": {x["feature"]: x["value"] for x in d["drivers"]}},
        output={k: d[k] for k in ("denial_risk", "risk_band", "top_reasons", "drivers")},
        model_name="denial_model", model_version=d["model_version"])
    return d


# --------------------------------------------------------------------------- appeal
class AppealIn(BaseModel):
    claim_id: str


@app.post("/api/appeal")
def appeal(body: AppealIn):
    from appeal.agent import run_appeal
    try:
        return run_appeal(body.claim_id)
    except KeyError:
        raise HTTPException(404, "claim not found")


# --------------------------------------------------------------------------- orchestrator
@app.post("/api/process/{claim_id}")
def process(claim_id: str):
    from api.orchestrator import process_claim
    try:
        return process_claim(claim_id)
    except KeyError:
        raise HTTPException(404, "claim not found")


# --------------------------------------------------------------------------- audit
@app.get("/api/audit")
def audit_list(module: str | None = None, subject_id: str | None = None, limit: int = Query(100, le=1000)):
    return audit.list_decisions(limit=limit, module=module, subject_id=subject_id)


@app.get("/api/audit/verify")
def audit_verify():
    return audit.verify_chain()


@app.get("/api/audit/{decision_id}")
def audit_get(decision_id: str):
    d = audit.get_decision(decision_id)
    if not d:
        raise HTTPException(404, "decision not found")
    return d
