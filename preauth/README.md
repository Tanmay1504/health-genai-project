# preauth/: FHIR record → pre-authorisation draft (LangGraph agent)

## What it does
| File | Role |
|---|---|
| `fhir_reader.py` | Opens one patient's FHIR bundle and extracts patient, encounter, diagnoses (with *first* diagnosis date), the billed service and amount, and Coverage. Falls back to the parser's synthesized coverage for Synthea data. |
| `schema.py` | Pydantic models for the request, and `to_fhir_claim()`, which emits a FHIR R4 `Claim` with `use = "preauthorization"`. |
| `agent.py` | The LangGraph agent. |

```
START → load_fhir → map_fields → check_rules ─┬─(has coverage)→ retrieve → justify ─┐
                                              └─(no coverage)───────────────────────┴→ assemble → audit → END
```
| Node | Deterministic? | Does |
|---|---|---|
| `load_fhir`, `map_fields` | yes | FHIR → form fields |
| `check_rules` | yes | `data/reference.evaluate_policy`: covered? pre-auth required? PED status? sub-limit? |
| `retrieve` | yes | RAG: plan-filtered policy clauses + clinical guidance |
| `justify` | **LLM** | required-justification checklist, medical-necessity draft, missing info, citations (JSON) |
| `assemble`, `audit` | yes | builds `PreAuthRequest`; writes the audit record |

**Why split it this way:** every number and date on the form is copied or computed, never generated. The LLM only writes the justification, and citations are filtered to chunk ids that were actually retrieved. With `LLM_PROVIDER=mock`, or when the model fails, a template justification is used and `generated_by="template"` says so.

## How it fits the whole
Called by the API (`POST /api/preauth`) and by the orchestrator when a claim needed pre-auth but has none. Uses `core/rag.py`, `core/llm.py`, `data/reference.py` and `trust/audit.py`.

## How to run
```bash
PID=$(python -c "import pandas as pd;print(pd.read_csv('data/processed/encounters.csv').query('encounter_class==\"IMP\"').patient_id.iloc[0])")
python -m preauth.agent $PID                 # latest inpatient stay
python -m preauth.agent $PID <encounter_id>  # a specific encounter
```

## How to verify
- Output JSON has `status: DRAFT`, a populated `coverage_check`, and `fhir_claim.use == "preauthorization"`.
- `citations` reference real chunk ids (e.g. `suraksha_silver_policy#7`).
- `python -m trust.audit --tail 1` shows a `preauth` record.
