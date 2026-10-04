# trust/: traceable, tamper-evident decision records

## What it does
`audit.py` stores **one row per decision** in SQLite (`trust/audit.db`):

| Column | Meaning |
|---|---|
| `decision_id`, `created_at` | UUID + UTC timestamp |
| `module`, `subject_type`, `subject_id` | who decided (preauth / fraud / denial / appeal / orchestrator) and about what |
| `recommendation` | e.g. `HIGH_RISK`, `APPEAL_STRONG`, `HOLD_REFER_TO_SIU` |
| `model_name`, `model_version` | e.g. `denial-xgb-20261003…-7e8a8fc8` from `brain/models/metadata.json` |
| `llm_provider`, `llm_model` | e.g. `ollama/llama3.1:8b`, or `template/none` when no LLM output was used |
| `inputs_json` | the facts the decision used |
| `retrieval_json` | chunk ids, sources, scores (and text for appeals) given to the LLM |
| `output_json` | the full structured result |
| `prev_hash`, `record_hash` | the **hash chain** |

`record_hash = SHA-256(prev_hash + canonical JSON of the row)`. Changing, deleting or reordering any record breaks every hash after it, and `verify_chain()` reports the first broken record. Writes use `BEGIN IMMEDIATE` so concurrent API requests cannot fork the chain.

## How it fits the whole
Every module calls `record_decision(...)`. The API exposes `GET /api/audit`, `/api/audit/{id}` and `/api/audit/verify`, and the UI's **Audit log** view shows them.

## How to run
```bash
python -m trust.audit --verify           # {'valid': True, 'records_checked': N, ...}
python -m trust.audit --tail 10
```

## How to verify (try tampering)
```bash
sqlite3 trust/audit.db "UPDATE decisions SET recommendation='APPROVE' WHERE seq=1"
python -m trust.audit --verify           # valid: False, first_broken_seq: 1
rm trust/audit.db                        # start a fresh log afterwards
```
`tests/test_units.py::test_audit_chain_detects_tampering` automates this check.
