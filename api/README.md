# api/: FastAPI backend + the LangGraph orchestrator

## What it does
| File | Role |
|---|---|
| `main.py` | REST endpoints the UI calls. It translates HTTP into module calls and contains no business logic. Swagger UI at `/docs`. |
| `orchestrator.py` | The **master LangGraph** that routes one claim between modules. |
| `Dockerfile` | Container for `docker compose --profile full`. |

### Orchestrator routing
```
fraud_screen ─(fraud ≥ 0.7)→ refer_siu ───────────────────────────┐
     └→ preauth_gate ─(pre-auth needed & missing)→ draft_preauth ─┐ │
             └──────────────────────────────────────────────────→ denial_predict
                   ├─(not denied & risk < 0.5)──────────→ submit
                   ├─(denied/high risk, rule violated)──→ fix_and_resubmit
                   └─(denied/high risk, compliant)──────→ appeal        ──→ finalize (audit) → END
```
- Fraud comes first, so the system never argues *for* paying a suspicious claim.
- An appeal happens only when the rulebook says the claim is compliant. That combination is the definition of a likely **wrongful** denial.

### Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | provider, Neo4j status, row counts |
| GET | `/api/models` | model versions + metrics |
| GET | `/api/claims?view=all\|high_risk\|fraud\|denied\|wrongful&q=` | claim list with scores |
| GET | `/api/claims/{id}` | claim detail |
| GET | `/api/patients`, `/api/patients/{id}/encounters` | pre-auth pickers |
| POST | `/api/preauth` `{patient_id, encounter_id?}` | pre-auth draft |
| GET | `/api/fraud/rings` | ring candidates + subgraphs |
| GET | `/api/fraud/claim/{id}` | fraud score + drivers (audited) |
| GET | `/api/denial/{id}` | risk, reasons, SHAP (audited) |
| POST | `/api/appeal` `{claim_id}` | appeal letter (audited) |
| POST | `/api/process/{id}` | full orchestrator run |
| GET | `/api/audit`, `/api/audit/verify`, `/api/audit/{decision_id}` | audit log |

## How to run
```bash
uvicorn api.main:app --reload --port 8000
python -m api.orchestrator <claim_id>          # orchestrator without HTTP
```

## How to verify
```bash
curl -s localhost:8000/api/health
curl -s "localhost:8000/api/claims?view=wrongful&limit=1"
curl -s -X POST localhost:8000/api/process/<claim_id>
```
Each of the four routes can be triggered: a planted-fraud claim gives `HOLD_REFER_TO_SIU`, a wrongful D03 denial gives `APPEAL_STRONG`, an outpatient claim on `JAC-BASIC` gives `FIX_AND_RESUBMIT`, and a low-risk approved claim gives `SUBMIT`.
