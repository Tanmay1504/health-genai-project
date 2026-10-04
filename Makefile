# =============================================================================
# HEALTH_GEN_AI — one command per pipeline stage, in run order.
#   make help          list targets
#   make all           full pipeline with Synthea + Neo4j (needs Java 17 + Docker)
#   make all-offline   full pipeline without Synthea/Docker (fallback generator + networkx)
# Every target is a thin wrapper around `python -m ...`. Read the module to
# see what a stage does.
# =============================================================================
PY      ?= .venv/bin/python
PATIENTS ?= 250

.PHONY: help setup neo4j-up neo4j-down neo4j-native-up neo4j-native-down synthea generate plant parse adjudicate data \
        graph-load fraud-queries analytics analytics-offline train index \
        api ui-install ui test verify-audit all all-offline clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n",$$1,$$2}'

PYTHON3 ?= python3.12

setup: ## create .venv, install Python deps, copy .env
	$(PYTHON3) -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt
	@test -f .env || cp .env.example .env

neo4j-up: ## start Neo4j (+GDS) in Docker and wait until healthy
	docker compose up -d neo4j
	@echo "waiting for Neo4j..."; until curl -s localhost:7474 >/dev/null; do sleep 2; done; echo "Neo4j up: http://localhost:7474"

neo4j-down: ## stop Neo4j
	docker compose down

neo4j-native-up: ## start Neo4j without Docker (~/.local/opt install)
	bash graph/neo4j_native.sh start

neo4j-native-down: ## stop the no-Docker Neo4j
	bash graph/neo4j_native.sh stop

synthea: ## generate Synthea FHIR patients (Java 17+)
	bash data/fetch_synthea.sh $(PATIENTS)

generate: ## fallback: Synthea-shaped FHIR without Java
	$(PY) -m data.generate_synthetic_fhir --patients 400

plant: ## inject the two fraud rings (FHIR)
	$(PY) -m data.plant_fraud_rings

parse: ## FHIR bundles -> tidy CSV tables
	$(PY) -m data.parse_fhir

adjudicate: ## simulate insurer decisions -> denial labels
	$(PY) -m data.simulate_adjudication

data: plant parse adjudicate ## plant + parse + adjudicate (run after synthea or generate)

graph-load: ## load CSVs into Neo4j
	$(PY) -m graph.load_graph --reset

fraud-queries: ## run the Cypher fraud-ring queries
	$(PY) -m graph.run_fraud_queries

analytics: ## Louvain/PageRank (GDS) + graph features + ring candidates
	$(PY) -m graph.graph_analytics --backend neo4j

analytics-offline: ## same, using networkx instead of Neo4j
	$(PY) -m graph.graph_analytics --backend networkx

train: ## train denial / reason / fraud models
	$(PY) -m brain.train

index: ## embed the policy + clinical corpus into Chroma
	$(PY) -m core.rag --rebuild

api: ## run the FastAPI backend on :8000 (restarts on code or .env changes)
	$(PY) -m uvicorn api.main:app --reload --reload-include .env --port 8000

ui-install: ## install UI dependencies
	cd ui && npm install

ui: ## run the Next.js UI on :3000
	cd ui && npm run dev

test: ## run the test suite (offline, LLM_PROVIDER=mock)
	LLM_PROVIDER=mock $(PY) -m pytest -q tests

verify-audit: ## check the audit log hash chain
	$(PY) -m trust.audit --verify --tail 10

all: synthea data neo4j-up graph-load fraud-queries analytics train index ## full pipeline (Synthea + Neo4j)

all-offline: generate data analytics-offline train index ## full pipeline without Java/Docker

clean: ## delete generated data, models, vectors, audit log
	rm -rf data/synthea_output data/generated_fhir data/planted_fhir data/processed data/chroma brain/models trust/audit.db
