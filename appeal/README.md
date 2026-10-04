# appeal/: agentic RAG that drafts a cited appeal letter

## What it does
`agent.py` is a LangGraph agent with **two loops**:

```
START → gather_context → plan_queries → retrieve → grade ─┬─(≥2 policy chunks)→ draft → verify ─┬─(clean)→ finalize → END
                                           ▲               │                       ▲            │
                                           └── rewrite ◀───┘(too few, ≤2 tries)    └─ revise ◀──┘(issues, 1 revision)
```

| Node | Deterministic? | Does |
|---|---|---|
| `gather_context` | yes | claim facts, actual or predicted denial reason; **re-runs the rulebook** to build the *grounds* and decide whether the appeal is viable |
| `plan_queries` | LLM (fallback: per-reason queries) | the agent writes its own search queries |
| `retrieve` | yes | plan-filtered policy chunks + clinical chunks |
| `grade` | LLM (fallback: nearest by distance) | keeps only useful evidence; invented ids are dropped |
| `rewrite` | LLM | broader queries if evidence is thin (corrective RAG) |
| `draft` | LLM (fallback: template) | letter citing `[chunk_id]` |
| `verify` | **yes, no LLM** | unknown citations? argument paragraphs without citations? claim id missing? |
| `finalize` | yes | strips any invalid citation, sets strength `STRONG` / `NEEDS_REVIEW` / `WEAK`, writes the audit record with the full retrieval context |

**Why agentic rather than one-shot RAG?** The decisive clause, such as the PED *definition* in clause 2.2, is not always the top hit for the obvious query. Planning, grading and rewriting raise the chance of finding it. Verifying in code means a hallucinated citation can never reach the final letter.

**Viability is decided by code, not the LLM.** If the insurer's cited rule genuinely applies, the letter is marked `WEAK`, and the orchestrator routes the claim to *fix and resubmit* instead.

## How it fits the whole
Called by `POST /api/appeal` and by the orchestrator when a claim is denied or high-risk **and** policy-compliant **and** not suspected fraud.

## How to run
```bash
CLAIM=$(python -c "import pandas as pd;c=pd.read_csv('data/processed/claims_labeled.csv');print(c[(c.denial_wrongful==1)&(c.denial_reason=='D03')].claim_id.iloc[0])")
python -m appeal.agent $CLAIM
```

## How to verify
- On a wrongful D03 denial: `appeal_strength: STRONG`, and citations include the plan's **Pre-Existing Disease definition** and the **burden-of-proof** clause (`claims_procedure_and_grievance`).
- `trace.retrieval_attempts` and `trace.revisions` show the loops in action.
- `pytest tests/test_pipeline_smoke.py -k hallucinated` runs a scripted LLM whose first draft cites a fake source. The verifier catches it and the revision fixes it.
