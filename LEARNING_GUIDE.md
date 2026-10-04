# LEARNING_GUIDE: read the system data → UI

The files are listed in the order that makes each one understandable using only what you have read before. For every stop there is **what to look for** and a **try it** command, so you can see the code's effect before moving on. Budget roughly 6–8 hours for a careful first pass.

Before you start: complete MASTER_README steps 0–7 once (or `make all-offline`), so every "try it" works. Use `LLM_PROVIDER=mock` while reading; switch to Ollama or OpenAI when you reach the agents.

---

## Part 0: Orientation (20 min)
| # | File | Look for |
|---|---|---|
| 0.1 | `MASTER_README.md` §1 Architecture | the data-flow diagram; keep it open in a tab |
| 0.2 | `core/config.py` | every knob and every path in one place; why no other file calls `os.getenv` |
| 0.3 | `.env.example` | the LLM switch and what each provider needs |

## Part 1: Data: from FHIR to labeled claims (90 min)
| # | File | Look for | Try it |
|---|---|---|---|
| 1.1 | `data/reference.py` | **Read this first in data/.** Reason codes D01–D07, the three plans, `evaluate_policy()`. Every later module leans on it. | `python -c "from data.reference import PLANS; print(PLANS['SGH-SILVER'])"` |
| 1.2 | `data/knowledge/policies/suraksha_silver_policy.md` | clause 2.2 (PED **definition**) and clause 4.2 (waiting period). The appeal story hinges on these. | read it |
| 1.3 | `data/fhir_builders.py` | the FHIR resource shapes; `urn:uuid` vs conditional references; `REF` = referrer | — |
| 1.4 | `data/generate_synthetic_fhir.py` | households, moonlighting doctors, chronic onset vs policy start (each a realism knob) | open one file in `data/generated_fhir/` |
| 1.5 | `data/fetch_synthea.sh` | the real-data path; why Java 17 | — |
| 1.6 | `data/plant_fraud_rings.py` | the two ring patterns + camouflage + the answer key | `cat data/planted_fhir/ground_truth.json \| head -30` |
| 1.7 | `data/parse_fhir.py` | `RefIndex` (three reference styles), primary-diagnosis resolution, first-onset date, USD→INR, synthesized coverage | `python -m data.parse_fhir` |
| 1.8 | `data/simulate_adjudication.py` | rules + leniency + **the PED bug** → `denial_wrongful` | `python -m data.simulate_adjudication` |

**Checkpoint:** explain why a diabetes claim can be *wrongly* denied with D03.

## Part 2: Graph: seeing collusion (90 min)
| # | File | Look for | Try it |
|---|---|---|---|
| 2.1 | `graph/db.py` | one long-lived driver; connectivity checks | — |
| 2.2 | `graph/load_graph.py` | the graph model docstring; `UNWIND` batching; `FOREACH`+`CASE` conditional MERGE; identity nodes; derived weighted relationships | `python -m graph.load_graph --reset` |
| 2.3 | `graph/fraud_queries.cypher` | the six pattern queries, each with its WHY; the GDS projection and why it is weighted | paste `referral_cycles` into Neo4j Browser |
| 2.4 | `graph/run_fraud_queries.py` | the `.cypher` file is the single source of truth | `python -m graph.run_fraud_queries` |
| 2.5 | `graph/graph_analytics.py` | two community backends producing the same table; per-claim features; ring score = mean of the top-2 patterns; evaluation | `python -m graph.graph_analytics` then compare with `--backend networkx` |

**Checkpoint:** in Neo4j Browser, compare node properties `community` (Louvain) and `component` (WCC) for a RING-B patient. Why does only one isolate the ring?

## Part 3: Brain: predicting and explaining (60 min)
| # | File | Look for | Try it |
|---|---|---|---|
| 3.1 | `brain/features.py` | raw rule *inputs* (not outcomes); fixed one-hot vocabularies; no leakage in fraud features | — |
| 3.2 | `brain/train.py` | two-stage denial (risk × reason); `scale_pos_weight`; PR-AUC; claim-only vs graph baseline; model versions | `python -m brain.train` |
| 3.3 | `brain/predict.py` | TreeSHAP via `pred_contribs`; reason probability = P(denied) × P(r\|denied) | `python -m brain.predict <wrongful claim id>`; note `chronic` ↑ and `onset_before_cover=0` ↓ |

**Checkpoint:** why did the graph features raise fraud PR-AUC, and why is 1.0 not a realistic number?

## Part 4: Trust: the audit trail (20 min)
| # | File | Look for | Try it |
|---|---|---|---|
| 4.1 | `trust/audit.py` | the record schema; canonical JSON; the hash chain; `BEGIN IMMEDIATE` | the tamper exercise in `trust/README.md` |

Read this before the agents: they all end by calling `record_decision`.

## Part 5: LLM + RAG foundations (45 min)
| # | File | Look for | Try it |
|---|---|---|---|
| 5.1 | `core/llm.py` | one interface, three providers; `complete_json` + default; graceful failure; `describe()` for audit | — |
| 5.2 | `core/rag.py` | header-based chunking (one clause per chunk); stable chunk ids; plan filter; per-embedder collections | `python -m core.rag --query "pre-existing disease definition" --plan SGH-SILVER` |
| 5.3 | `data/knowledge/policies/claims_procedure_and_grievance.md` + one file in `data/knowledge/clinical/` | the denial standards the appeal agent leans on | — |

## Part 6: Agents (LangGraph) (90 min)
| # | File | Look for | Try it |
|---|---|---|---|
| 6.1 | `preauth/fhir_reader.py` | real FHIR navigation for one patient | — |
| 6.2 | `preauth/schema.py` | which fields are FHIR, which are rules, which are LLM; `to_fhir_claim()` | — |
| 6.3 | `preauth/agent.py` | **your first LangGraph**: `TypedDict` state, nodes return partial updates, one conditional edge | `python -m preauth.agent <patient_id>` |
| 6.4 | `appeal/agent.py` | **agentic RAG**: two loops (rewrite, revise), LLM-as-grader, deterministic `verify`, viability decided in code | `python -m appeal.agent <wrongful D03 claim>` with `mock`, then with `ollama`; compare letters |
| 6.5 | `tests/test_pipeline_smoke.py::_ScriptedChat` | how the verifier catches a hallucinated citation | `pytest -q tests -k hallucinated` |

**Checkpoint:** draw the appeal graph from memory and mark which nodes may call the LLM.

## Part 7: Orchestration and API (40 min)
| # | File | Look for | Try it |
|---|---|---|---|
| 7.1 | `api/orchestrator.py` | the router graph; why fraud comes first; "compliant + high risk = wrongful" | trigger all four outcomes (see `api/README.md`) |
| 7.2 | `api/main.py` | thin HTTP layer; audited endpoints; the readable-503 exception handler | `uvicorn api.main:app --reload` → `/docs` |

## Part 8: UI (45 min)
| # | File | Look for |
|---|---|---|
| 8.1 | `ui/next.config.mjs` | `/api` rewrite proxy; `proxyTimeout` |
| 8.2 | `ui/lib/api.ts` | the typed contract mirroring Python responses |
| 8.3 | `ui/app/layout.tsx`, `components/Nav.tsx`, `app/globals.css` | shell + theme tokens |
| 8.4 | `ui/app/page.tsx` | claims list + orchestrator "Run" |
| 8.5 | `ui/app/denial/page.tsx` | rendering SHAP as signed bars |
| 8.6 | `ui/components/ForceGraph.tsx` → `ui/app/fraud/page.tsx` | d3-force layout computed once, rendered as SVG |
| 8.7 | `ui/app/preauth/page.tsx`, `ui/app/appeal/page.tsx` | provenance on screen: FHIR vs rules vs LLM; clickable citations |
| 8.8 | `ui/app/audit/page.tsx` | the decision record, expanded |

## Part 9: Ops (15 min)
`docker-compose.yml` (Neo4j + GDS; the optional `full` profile), `Makefile`, `requirements.txt`, `.gitignore` (why data and `.env` never get committed).

---

## Exercises (in increasing difficulty)
1. Add a fourth plan to `data/reference.py` and its policy markdown, rebuild the index, and re-run the pipeline.
2. Change the insurer's PED-bug probability in `simulate_adjudication.py` to 0. What happens to `wrongful_denials_flagged@0.5` and to appeals?
3. Write a Cypher query for **upcoding**: one doctor billing IMP far more often than peers for the same diagnosis.
4. Add a graph feature for it in `graph_analytics.py`, retrain, and compare fraud PR-AUC.
5. Add a node to the appeal graph that asks the LLM to critique the letter's tone, routing back to `draft` at most once.
6. Swap `LLM_PROVIDER` between `ollama` and `openai` and compare the same appeal in `/audit` (the `llm_model` column).
