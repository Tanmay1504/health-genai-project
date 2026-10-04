# tests/

Offline tests. `conftest.py` forces `LLM_PROVIDER=mock` and a throwaway audit DB, so they never touch your real log, any API key or a running model.

| File | Covers | Needs pipeline? |
|---|---|---|
| `test_units.py` | rule engine (PED before/after cover, sub-limits, hospital-only plan), FHIR reference resolution, JSON extraction from messy LLM output, audit tamper detection | no |
| `test_pipeline_smoke.py` | rings recovered, denial explanations, STRONG cited appeal, pre-auth FHIR output, orchestrator fraud hold, API endpoints, and the **LLM path** via a scripted fake model that hallucinates a citation | yes (auto-skipped otherwise) |

```bash
LLM_PROVIDER=mock pytest -q tests
```
