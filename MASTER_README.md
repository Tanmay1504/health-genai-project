# HEALTH_GEN_AI: GenAI for Indian health-insurance claims

An end-to-end learning platform that, for every health-insurance claim:

| # | Capability | What it does | Main tech |
|---|---|---|---|
| 1 | **Pre-auth** | Reads the patient's FHIR record and auto-fills a pre-authorisation request (patient, coverage, encounter, diagnosis). Checks coverage with a rule engine and drafts the justification the policy requires, with citations. | LangGraph, RAG (Chroma), FHIR R4 |
| 2 | **Fraud rings** | Finds colluding clinics, doctors and patients in a graph through shared identities, doctor–clinic meshes, referral loops and threshold structuring. | Neo4j, Cypher, GDS Louvain/PageRank |
| 3 | **Denial prediction** | Predicts P(denied), the top reason codes and the per-claim SHAP drivers. | XGBoost, TreeSHAP |
| 4 | **Auto-appeal** | For legitimate claims likely to be wrongly denied, an agent plans searches, grades evidence, drafts a cited appeal letter and verifies its own citations. | LangGraph agentic RAG |
| 5 | **Trust / audit** | Writes a tamper-evident decision record for every decision: inputs, model version, LLM, retrieval context, recommendation and timestamp. | SQLite + SHA-256 hash chain |

A **Next.js UI** has one view per capability. A **LangGraph orchestrator** routes each claim between the modules.

> ⚠️ **Synthetic data only.** Patients come from Synthea or a built-in generator. The insurer ("Suraksha General Health Insurance") and the scheme ("Jan Arogya Cover") are fictional, and so are their policy wordings in `data/knowledge/`. Clinical notes are illustrative, **not medical advice**. Never load real patient data into this project.

---

## 1. Architecture

```
                          ┌──────────────────────────── data/ ────────────────────────────┐
  Synthea (Java)  ──FHIR──▶  synthea_output/fhir/*.json      (or generate_synthetic_fhir.py)│
                          │          │                                                      │
                          │  plant_fraud_rings.py ──FHIR──▶ planted_fhir/ + ground_truth   │
                          │          ▼                                                      │
                          │  parse_fhir.py ──▶ processed/*.csv (patients, encounters,      │
                          │          │          conditions, coverages, claims, ...)         │
                          │          ▼                                                      │
                          │  simulate_adjudication.py ──▶ claims_labeled.csv               │
                          │          (insurer rules + leniency + a deliberate "bug")        │
                          └──────────┬──────────────────────────────────┬──────────────────┘
                                     │                                  │
                     ┌─────── graph/ ▼ ───────┐               ┌─ core/rag.py ─────────────┐
                     │ load_graph.py → Neo4j  │               │ knowledge/*.md → chunks   │
                     │ fraud_queries.cypher   │               │ → embeddings → Chroma     │
                     │ graph_analytics.py     │               │ (OpenAI | Ollama | mock)  │
                     │  (GDS Louvain/PageRank │               └────────────┬──────────────┘
                     │   or networkx)         │                            │
                     └───┬───────────────┬────┘                            │
        graph_features.csv            fraud_rings.json                     │
                         ▼               │                                 │
                 ┌──── brain/ ─────┐     │       ┌── preauth/ ─────────┐   │
                 │ features.py     │     │       │ LangGraph agent:    │◀──┤
                 │ train.py (XGB)  │     │       │ FHIR→form→rules→RAG │   │
                 │ predict.py+SHAP │     │       │ →LLM→draft          │   │
                 └───────┬─────────┘     │       └─────────┬───────────┘   │
                         │               │       ┌── appeal/ ▼─────────┐   │
                         │               │       │ agentic RAG loop:   │◀──┘
                         │               │       │ plan→retrieve→grade │
                         │               │       │ →(rewrite)→draft→   │
                         │               │       │ verify→(revise)     │
                         │               │       └─────────┬───────────┘
                         ▼               ▼                 ▼
                 ┌──────────────── api/ (FastAPI) ─────────────────────┐
                 │ orchestrator.py: LangGraph router                   │
                 │   fraud_screen → preauth_gate → denial_predict →    │
                 │   {submit | fix_and_resubmit | appeal | refer_siu}  │──▶ trust/audit.py
                 │ main.py: REST endpoints                             │     (SQLite, hash chain)
                 └──────────────────────────┬──────────────────────────┘
                                            │ /api/* (proxied)
                                 ┌──────────▼──────────┐
                                 │ ui/ (Next.js 15)    │ Claims · Pre-auth · Fraud rings
                                 │                     │ Denial risk · Appeal · Audit log
                                 └─────────────────────┘
```

**Folders by concern**

| Folder | Concern | README |
|---|---|---|
| `core/` | Config, swappable LLM/embeddings interface, RAG over Chroma | [core/README.md](core/README.md) |
| `data/` | Synthea fetch, fallback generator, fraud planting, FHIR parsing, adjudication simulation, RAG corpus | [data/README.md](data/README.md) |
| `graph/` | Neo4j loading, Cypher fraud queries, GDS analytics, graph features | [graph/README.md](graph/README.md) |
| `brain/` | Feature engineering, XGBoost denial/reason/fraud models, SHAP explanations | [brain/README.md](brain/README.md) |
| `preauth/` | FHIR reader, pre-auth schema, LangGraph pre-auth agent | [preauth/README.md](preauth/README.md) |
| `appeal/` | Agentic-RAG appeal agent | [appeal/README.md](appeal/README.md) |
| `trust/` | Audit log with hash chain | [trust/README.md](trust/README.md) |
| `api/` | FastAPI + LangGraph orchestrator that routes between modules | [api/README.md](api/README.md) |
| `ui/` | Next.js app | [ui/README.md](ui/README.md) |
| `tests/` | Unit and end-to-end smoke tests (offline) | [tests/README.md](tests/README.md) |

`core/` and `api/` were added beyond the requested folders. The LLM switch and the RAG layer are shared by pre-auth and appeal, and the orchestrator plus HTTP layer sits between all modules and the UI. Neither belongs inside a single module.

---

## 2. Tool versions targeted (and tested together, Oct 2026)

| Tool | Version | Notes |
|---|---|---|
| macOS / chip | 13+ on Apple Silicon (M1/M3) | everything runs native arm64 |
| Python | **3.12** (3.11 works) | `brew install python@3.12` |
| libomp | latest | **required by XGBoost on macOS**: `brew install libomp` |
| Java | **17+** (Synthea; Neo4j native uses 21) | Java 8 will not work: `brew install openjdk@17` or the tarball route |
| Docker | Docker Desktop 4.x **or** Colima | runs Neo4j |
| Neo4j | **5.26 LTS community** + GDS **2.13** | via `docker-compose.yml` (GDS auto-installed) |
| Node.js | **22 LTS** (20+ works) | `brew install node@22` |
| Next.js / React | 15.5 / 19 | |
| Ollama | 0.35 | optional; `llama3.1:8b` + `nomic-embed-text`. **Does not work on macOS 13**: its Metal backend hits `GGML_ASSERT(buf_dst)` while loading a model, and CPU mode spends ~45 min in GPU discovery |
| Groq | API | `LLM_PROVIDER=groq`, default model `openai/gpt-oss-120b` (Groq retired `llama-3.3-70b-versatile`; list yours at console.groq.com) |
| FastEmbed | 0.8 | local CPU embeddings (`BAAI/bge-small-en-v1.5`), default with Groq |
| Python libs | langgraph 1.2, langchain-core 1.6, langchain-openai 1.6, langchain-ollama 1.1, langchain-chroma 1.1, chromadb 1.5, xgboost 3.4, scikit-learn 1.9, pandas 3.0, neo4j driver 6.3, networkx 3.7, FastAPI 0.142 | ranges in `requirements.txt` |

---

## 3. Setup and run, in this exact order

All commands run from the repo root (`HEALTH_GEN_AI/`). Each step names the folder README to read if you want to understand it first. A `Makefile` wraps every step (`make help`).

### Step 0: One-time machine prerequisites (Homebrew)

```bash
brew install python@3.12 libomp openjdk@17 node@22 ollama
brew install --cask docker          # Docker Desktop: open it once so the engine starts
#   (alternative: brew install colima docker docker-compose && colima start --cpu 4 --memory 8)
echo 'export PATH="$(brew --prefix node@22)/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc   # node@22 is keg-only
```

#### No-Homebrew / no-Docker route (macOS 13, or any Mac where Step 0 fails)
Homebrew has **no prebuilt packages for macOS 13** (it falls back to compiling Rust, Go, etc. from source for hours), and current Docker Desktop no longer supports macOS 13. Use official prebuilt tarballs in `~/.local/opt` instead. No admin password is needed, and this is exactly how the reference machine (M-series, macOS 13.3, 8 GB) runs:

```bash
mkdir -p ~/.local/opt && cd ~/.local/opt
# Java 21 (Synthea + Neo4j)
curl -L -o jdk.tgz "https://api.adoptium.net/v3/binary/latest/21/ga/mac/aarch64/jdk/hotspot/normal/eclipse" && tar xzf jdk.tgz && mv jdk-21* jdk-21
# Neo4j 5.26 + Graph Data Science plugin
curl -L -o neo4j.tgz https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz && tar xzf neo4j.tgz && mv neo4j-community-5.26.0 neo4j
curl -L -o gds.zip https://graphdatascience.ninja/neo4j-graph-data-science-2.13.2.zip && unzip -o gds.zip -d neo4j/plugins/
printf 'dbms.security.procedures.unrestricted=gds.*\ndbms.security.procedures.allowlist=gds.*\nserver.memory.heap.max_size=1g\n' >> neo4j/conf/neo4j.conf
JAVA_HOME=~/.local/opt/jdk-21/Contents/Home neo4j/bin/neo4j-admin dbms set-initial-password change-me-local-only
# Node 22
V=$(curl -s https://nodejs.org/dist/index.json | python3 -c "import json,sys;print(next(r['version'] for r in json.load(sys.stdin) if r['version'].startswith('v22.')))")
curl -L -o node.tgz https://nodejs.org/dist/$V/node-$V-darwin-arm64.tar.gz && tar xzf node.tgz && mv node-$V-darwin-arm64 node-22
echo 'export PATH="$HOME/.local/opt/node-22/bin:$PATH"' >> ~/.zshrc
# XGBoost's OpenMP runtime: copy the one bundled in scikit-learn next to your Python
#   (after Step 1's pip install):  cp .venv/lib/python3.12/site-packages/sklearn/.dylibs/libomp.dylib "$(python3.12 -c 'import sys;print(sys.base_prefix)')/lib/"
```
Then use `make neo4j-native-up` instead of `docker compose up -d neo4j`. Use **`LLM_PROVIDER=groq`** (hosted, needs an API key) instead of Ollama: Ollama's Metal backend crashes on macOS 13, and an 8B local model doesn't fit next to Neo4j in 8 GB of RAM anyway. Embeddings then run locally on the CPU via FastEmbed (no Ollama needed).

### Step 1: Python environment and config

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # then edit: choose LLM_PROVIDER, set NEO4J_PASSWORD
```

**Pick the LLM provider in `.env`.** Nothing else changes when you switch. Embeddings follow the provider unless you set `EMBEDDINGS_PROVIDER`. Groq has no embeddings API, so `groq` uses `local` embeddings (FastEmbed, a small CPU model).

| `LLM_PROVIDER` | Needs | Use when |
|---|---|---|
| `ollama` (default) | `brew services start ollama` then `ollama pull llama3.1:8b && ollama pull nomic-embed-text` | free, local, private |
| `groq` | `GROQ_API_KEY=...` in `.env` (embeddings run locally via FastEmbed, auto-downloaded) | fast hosted `gpt-oss-120b`; good quality, light on RAM. **Recommended on 8 GB Macs / macOS 13** |
| `openai` | `OPENAI_API_KEY=sk-...` in `.env` | best letter quality |
| `mock` | nothing | learning the plumbing offline and running tests. Agents use deterministic templates |

### Step 2: Start Neo4j (with Graph Data Science)

```bash
docker compose up -d neo4j
# wait ~30-60 s on first start (it downloads the GDS plugin), then:
open http://localhost:7474          # login: neo4j / <NEO4J_PASSWORD from .env>
```

### Step 3: Synthetic patients (choose ONE source)

```bash
# A) Synthea: realistic US-style histories, mapped to Indian plans. Needs Java 17.
bash data/fetch_synthea.sh 250 42           # 250 patients, seed 42 (~2-5 min)

# B) Fallback generator: Indian-context names/cities, no Java. ~2,200 claims.
python -m data.generate_synthetic_fhir --patients 400
```
If you switch sources later, run `make clean` first. The parser reads every FHIR folder that exists.

### Step 4: Plant fraud rings, parse FHIR, simulate the insurer  → [data/README.md](data/README.md)

```bash
python -m data.plant_fraud_rings        # injects RING-A and RING-B as FHIR + answer key
python -m data.parse_fhir               # FHIR → data/processed/*.csv
python -m data.simulate_adjudication    # adds denial labels → claims_labeled.csv
```

### Step 5: Graph: load, query, analyse  → [graph/README.md](graph/README.md)

```bash
python -m graph.load_graph --reset      # CSV → Neo4j nodes/relationships
python -m graph.run_fraud_queries       # Cypher pattern queries; both rings should appear
python -m graph.graph_analytics         # GDS Louvain + PageRank → graph_features.csv, fraud_rings.json
#   no Docker? python -m graph.graph_analytics --backend networkx   (same outputs, in Python)
```

### Step 6: Train the brain  → [brain/README.md](brain/README.md)

```bash
python -m brain.train                   # denial, reason and fraud models → brain/models/
python -m brain.predict                 # print a sample explanation
```

### Step 7: Build the RAG index  → [core/README.md](core/README.md)

```bash
python -m core.rag --rebuild            # embeds data/knowledge/ with your provider's embedder
python -m core.rag --query "pre-existing disease definition" --plan SGH-SILVER
```

### Step 8: Try the agents from the CLI (optional)  → [preauth/](preauth/README.md), [appeal/](appeal/README.md), [api/](api/README.md)

```bash
CLAIM=$(python -c "import pandas as pd;c=pd.read_csv('data/processed/claims_labeled.csv');print(c[c.denial_wrongful==1].claim_id.iloc[0])")
python -m appeal.agent $CLAIM           # agentic RAG appeal letter
python -m api.orchestrator $CLAIM       # full routing: fraud → pre-auth → denial → appeal
```

### Step 9: Run the tests

```bash
LLM_PROVIDER=mock pytest -q tests       # 17 tests, offline, uses a throwaway audit DB
```

### Step 10: Start the API and the UI (two terminals)

```bash
# terminal 1
uvicorn api.main:app --reload --port 8000        # Swagger UI: http://localhost:8000/docs

# terminal 2
cd ui && npm install && npm run dev               # http://localhost:3000
```

### Step 11: Verify the audit trail  → [trust/README.md](trust/README.md)

```bash
python -m trust.audit --verify --tail 10
```

**Shortcuts:** `make all` runs steps 3A–7 (Synthea + Neo4j). `make all-offline` runs steps 3B–7 with no Java and no Docker (networkx backend). The full stack can also run in containers with `docker compose --profile full up -d --build`, after running the data pipeline on the host.

---

## 4. What "good" looks like

**Real Synthea run** (250 patients requested → 294 + 24 planted ring members, seed 42, Neo4j-GDS backend):

| Check | Result |
|---|---|
| Claims / patients | 3,489 claims, 318 patients, 493 organisations, 497 practitioners |
| Insurer denial rate | 22% (88 wrongful denials) · median claim ₹10.7k, p90 ₹41k |
| Ring detection | **precision 1.0, recall 1.0** (both rings) |
| Denial model | ROC-AUC 0.96, PR-AUC 0.90 |
| Fraud model | PR-AUC 0.996 claim-only → 1.00 with graph. The planted ring claims (Indian clinics, unusual amounts) already stand out against US-style Synthea history, so the graph adds little *here*; on the fallback dataset it adds +0.10 |
| RAG (FastEmbed `bge-small`) | PED-definition clause is the top hit for "chronic condition diagnosed after policy start" |

**Fallback generator** (seed 42):

| Check | Result |
|---|---|
| Claims / patients | 2,233 claims, 424 patients (24 planted ring members) |
| Insurer denial rate | 33% (of which 62 are *wrongful* denials) |
| Cypher queries | the shared phone (10 patients), 2 shared addresses, the 4-doctor mesh, the 5-doctor referral loop and 5 structuring doctors are all found |
| Ring detection (Louvain, weighted) | **precision 1.0, recall 1.0** vs planted answer key; Neo4j-GDS and networkx agree |
| Denial model | ROC-AUC 0.95, PR-AUC 0.87 |
| Reason model | accuracy 0.97 |
| Fraud model | PR-AUC 0.90 with claim fields only → **1.00 with graph features** |
| Appeal on a wrongful pre-existing-disease denial | `STRONG`, cites the PED definition clause + the burden-of-proof clause |
| Audit chain | `valid: True` |

The fraud numbers are perfect because the planted rings are textbook-clean. Real fraud is noisier. Treat these numbers as "the plumbing works" rather than as a benchmark.

---

## 5. Design decisions worth knowing

1. **Facts are deterministic; the LLM only explains.** Amounts, dates, coverage rules and appeal *viability* come from code (`data/reference.py`). The LLM drafts prose and must cite retrieved chunk ids, and code verifies those ids.
2. **One LLM interface.** `core/llm.py:get_llm()` is the only place a provider is chosen. Every call degrades to a template on failure, and the audit log records which path was taken.
3. **The insurer has a known bug.** The simulator wrongly applies the pre-existing-disease waiting period to chronic conditions diagnosed *after* the policy started. The denial model learns the bug (see the SHAP drivers), and the appeal agent wins against it with the policy's own definition. That is the whole "wrongful denial → auto-appeal" story in one reproducible example.
4. **Fraud before appeal.** The orchestrator never drafts an appeal for a claim that scored as fraud.
5. **Weighted community detection.** Edges are weighted by interaction count. Without weights, one camouflage visit to a large hospital pulled a whole ring into that hospital's community.
6. **Ground truth is quarantined.** Planted labels live in `ground_truth.json` and `_truth_ring` properties. Detection code never reads them; only evaluation does.

## 6. Known limitations

- **Synthea is verified** (250 patients, seed 42): 100% of claims resolve to named hospitals and doctors. Synthea's US prices are scaled with `USD_TO_INR = 4`. About 58% of Synthea claims (check-ups, dental) carry no disorder diagnosis; they are treated as `general_medicine`.
- The **Ollama/OpenAI generation paths** are wired and unit-tested with a scripted fake model, but letter quality depends on the model you run. 8B local models are noticeably weaker than hosted ones.
- Denial labels are **simulated**, so model metrics describe this simulator, not any real insurer.
- The audit log's hash chain proves tampering happened; it cannot prevent it. Production would ship records to WORM storage.

## 7. Public demo (GitHub Pages)
GitHub Pages can only serve static files, so the public demo replays real outputs instead of running Python, Neo4j or an LLM:
```bash
python scripts/build_demo_snapshot.py     # calls the real API in-process, saves JSON to ui/public/demo/ (needs GROQ_API_KEY)
bash scripts/deploy_demo.sh               # static Next.js export (NEXT_PUBLIC_DEMO=1) → gh-pages branch
```
In demo mode `ui/lib/api.ts` answers every call from those JSON files. Groq's free tier allows 8K tokens/min; if a run stalls, re-run with `--resume` (see the script's docstring).

## 8. Where to start reading

See **[LEARNING_GUIDE.md](LEARNING_GUIDE.md)** for a file-by-file reading order, data → UI.
