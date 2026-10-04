# graph/: Neo4j graph, Cypher fraud-ring queries, graph analytics

## What it does
| File | Role |
|---|---|
| `db.py` | Neo4j driver helpers (`get_driver`, `run`, `neo4j_available`, `gds_available`). |
| `load_graph.py` | Loads `data/processed/*.csv` into Neo4j with batched `UNWIND … MERGE`, then builds the derived "shortcut" relationships. |
| `fraud_queries.cypher` | **All Cypher in one readable file**: six fraud-pattern queries and the GDS steps. Each query begins with `// name:`. |
| `run_fraud_queries.py` | Parses the `.cypher` file and runs queries by name (no second copy of the Cypher in Python). |
| `graph_analytics.py` | Louvain communities + PageRank (Neo4j GDS **or** networkx), per-claim graph features, ring scoring, evaluation against the planted answer key. |

## Graph model
```
(:Patient)-[:HAD_ENCOUNTER]->(:Encounter)-[:AT]->(:Organization)
(:Encounter)-[:ATTENDED_BY|REFERRED_BY]->(:Practitioner)
(:Encounter)-[:DIAGNOSED]->(:Condition)<-[:HAS_CONDITION]-(:Patient)
(:Patient)-[:HAS_COVERAGE]->(:Coverage)<-[:UNDER_COVERAGE]-(:Claim)
(:Patient)-[:FILED]->(:Claim)-[:FOR_ENCOUNTER|BILLED_BY|RENDERED_BY|HAS_DIAGNOSIS]->(...)
(:Patient)-[:LIVES_AT]->(:Address)      (:Patient)-[:HAS_PHONE]->(:Phone)        identity nodes
(:Patient)-[:VISITED]->(:Organization)  (:Patient)-[:SEEN_BY]->(:Practitioner)   derived, weighted
(:Practitioner)-[:WORKS_AT]->(:Organization)  (:Practitioner)-[:REFERRED_TO]->(:Practitioner)
```
Addresses and phones are **nodes**, so "who shares this phone?" is one hop. Derived relationships carry a `weight` (the interaction count) used by Louvain and PageRank.

## The fraud queries (`fraud_queries.cypher`)
| Query | Catches |
|---|---|
| `shared_phone_clusters` | ≥ 4 patients on one phone who also use the same clinics (RING-A) |
| `shared_address_clusters` | ≥ 5 patients at one address with ≥ 15 claims (RING-A) |
| `clinic_doctor_mesh` | clinic pairs sharing ≥ 2 doctors and ≥ 5 patients (both rings) |
| `referral_cycles` | directed referral loops of 3–6 doctors (RING-B) |
| `claim_velocity` | ≥ 5 claims in 90 days across ≥ 2 clinics (RING-A) |
| `threshold_structuring` | doctors with ≥ 50% of claims at 90–99% of the pre-auth threshold (RING-B) |

## How it fits the whole
Input: `data/processed/*.csv`. Outputs: `graph_features.csv`, which the `brain/` fraud model consumes, and `fraud_rings.json`, which the UI's Fraud rings view displays.

## How to run
```bash
docker compose up -d neo4j
python -m graph.load_graph --reset
python -m graph.run_fraud_queries            # or one: python -m graph.run_fraud_queries referral_cycles
python -m graph.graph_analytics              # auto: GDS if available, else networkx
python -m graph.graph_analytics --backend networkx   # no Docker needed
```

## How to verify
- `load_graph` prints node counts per label.
- `run_fraud_queries` should list the shared phone (10 patients), 2 addresses, the 4-doctor mesh, one 5-hop referral loop, and the 5 structuring doctors.
- `graph_analytics` prints ring candidates and `precision=1.0 recall=1.0` against the planted answer key on the fallback dataset.
- In Neo4j Browser, see a ring:
  ```cypher
  MATCH (p:Patient {_truth_ring:'RING-B'})-[r:SEEN_BY|VISITED]->(x) RETURN p, r, x
  ```
  Compare the `community` property (Louvain) with `component` (WCC). WCC lumps the rings in with legitimate hospitals because of the camouflage visits; Louvain separates them.
