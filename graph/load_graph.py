"""
graph/load_graph.py — load the processed FHIR tables into Neo4j as a graph.

    python -m graph.load_graph            # upsert (MERGE) everything
    python -m graph.load_graph --reset    # wipe the DB first

GRAPH MODEL (labels in CamelCase, relationships in UPPER_SNAKE)
-----------------------------------------------------------------
Core FHIR entities:

  (:Patient)-[:HAD_ENCOUNTER]->(:Encounter)-[:AT]->(:Organization)
  (:Encounter)-[:ATTENDED_BY]->(:Practitioner)       primary performer (PPRF)
  (:Encounter)-[:REFERRED_BY]->(:Practitioner)       referrer (REF)
  (:Encounter)-[:DIAGNOSED]->(:Condition)
  (:Patient)-[:HAS_CONDITION]->(:Condition)
  (:Patient)-[:HAS_COVERAGE]->(:Coverage)
  (:Patient)-[:FILED]->(:Claim)
  (:Claim)-[:FOR_ENCOUNTER]->(:Encounter)
  (:Claim)-[:BILLED_BY]->(:Organization)
  (:Claim)-[:RENDERED_BY]->(:Practitioner)
  (:Claim)-[:UNDER_COVERAGE]->(:Coverage)
  (:Claim)-[:HAS_DIAGNOSIS]->(:Condition)

Identity nodes (WHY: fraud rings reuse identities):
  (:Patient)-[:LIVES_AT]->(:Address)
  (:Patient)-[:HAS_PHONE]->(:Phone)
  Storing address/phone as *properties* would force string comparisons across
  all patient pairs (O(n²)). As *nodes*, "who shares this phone?" becomes
  a single hop, and community detection sees the shared identity as an edge.

Derived "shortcut" relationships (WHY: fraud analytics wants direct edges):
  (:Patient)-[:VISITED {visits}]->(:Organization)
  (:Patient)-[:SEEN_BY {visits}]->(:Practitioner)
  (:Practitioner)-[:WORKS_AT {encounters}]->(:Organization)
  (:Practitioner)-[:REFERRED_TO {count}]->(:Practitioner)
  These collapse Patient→Encounter→Org paths into one edge whose `weight`
  is the number of interactions (identity edges get weight 1). GDS
  algorithms (Louvain, PageRank) run on these collapsed edges, because
  Encounter/Claim nodes would otherwise dominate the graph without adding
  collusion signal.

Ground truth: planted-ring members carry a `_truth_ring` property so you can
eyeball them in Neo4j Browser. Detection queries NEVER read properties that
start with an underscore. That keeps the evaluation honest.
"""
from __future__ import annotations

import argparse
import math
import time

import pandas as pd

from core.config import PATHS
from graph.db import get_driver, neo4j_available

BATCH = 1000

# Uniqueness constraints double as indexes: every MERGE below looks nodes up
# by these keys, so without them each MERGE would be a full label scan.
CONSTRAINTS = [
    ("Patient", "id"), ("Organization", "id"), ("Practitioner", "id"), ("Encounter", "id"),
    ("Condition", "id"), ("Coverage", "id"), ("Claim", "id"), ("Address", "key"), ("Phone", "number"),
]


def _rows(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of dicts with NaN turned into None (Cypher null)."""
    out = []
    for rec in df.to_dict("records"):
        out.append({k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in rec.items()})
    return out


def _write(query: str, rows: list[dict], label: str) -> None:
    """Send rows in batches using UNWIND.

    WHY UNWIND batches: one transaction per row would mean thousands of network
    round-trips. `UNWIND $rows AS row` lets Neo4j process 1000 rows per
    round-trip inside one transaction: typically 50-100x faster.
    """
    t = time.time()
    with get_driver().session() as s:
        for i in range(0, len(rows), BATCH):
            s.execute_write(lambda tx, b: tx.run(query, rows=b).consume(), rows[i : i + BATCH])
    print(f"  {label:<38} {len(rows):>7,} rows  ({time.time() - t:.1f}s)")


# =============================================================================
# Cypher statements. Each is a MERGE so the loader is idempotent (re-running
# never duplicates data).
# =============================================================================
Q_PATIENTS = """
UNWIND $rows AS row
MERGE (p:Patient {id: row.patient_id})
SET p.name = row.name, p.gender = row.gender, p.birthDate = row.birth_date,
    p.city = row.city, p.source = row.source, p._truth_ring = row.ring_id
WITH p, row
// Address / phone become shared nodes. Empty strings are skipped so that all
// patients with a missing phone don't "share" one phantom Phone node.
FOREACH (_ IN CASE WHEN row.address_key <> '' THEN [1] ELSE [] END |
    MERGE (a:Address {key: row.address_key}) MERGE (p)-[la:LIVES_AT]->(a) SET la.weight = 1.0)
FOREACH (_ IN CASE WHEN row.phone IS NOT NULL AND row.phone <> '' THEN [1] ELSE [] END |
    MERGE (ph:Phone {number: row.phone}) MERGE (p)-[hp:HAS_PHONE]->(ph) SET hp.weight = 1.0)
"""

Q_ORGS = """
UNWIND $rows AS row
MERGE (o:Organization {id: row.org_id})
SET o.name = row.name, o.city = row.city, o.kind = row.kind
"""

Q_PRACTS = """
UNWIND $rows AS row
MERGE (d:Practitioner {id: row.practitioner_id})
SET d.name = row.name, d.regNo = row.reg_no, d.gender = row.gender
"""

Q_ENCOUNTERS = """
UNWIND $rows AS row
MATCH (p:Patient {id: row.patient_id})
MERGE (e:Encounter {id: row.encounter_id})
SET e.class = row.encounter_class, e.type = row.type, e.start = date(row.start)
MERGE (p)-[:HAD_ENCOUNTER]->(e)
// FOREACH+CASE is the idiomatic "conditional MERGE": OPTIONAL MATCH + MERGE
// would fail on null nodes.
WITH e, row
OPTIONAL MATCH (o:Organization {id: row.org_id})
OPTIONAL MATCH (d:Practitioner {id: row.practitioner_id})
OPTIONAL MATCH (r:Practitioner {id: row.referrer_id})
FOREACH (_ IN CASE WHEN o IS NULL THEN [] ELSE [1] END | MERGE (e)-[:AT]->(o))
FOREACH (_ IN CASE WHEN d IS NULL THEN [] ELSE [1] END | MERGE (e)-[:ATTENDED_BY]->(d))
FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END | MERGE (e)-[:REFERRED_BY]->(r))
"""

Q_CONDITIONS = """
UNWIND $rows AS row
MATCH (p:Patient {id: row.patient_id})
MERGE (c:Condition {id: row.condition_id})
SET c.code = row.code, c.display = row.display, c.onset = date(row.onset)
MERGE (p)-[:HAS_CONDITION]->(c)
WITH c, row
OPTIONAL MATCH (e:Encounter {id: row.encounter_id})
FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | MERGE (e)-[:DIAGNOSED]->(c))
"""

Q_COVERAGES = """
UNWIND $rows AS row
MATCH (p:Patient {id: row.patient_id})
MERGE (c:Coverage {id: row.coverage_id})
SET c.planCode = row.plan_code, c.start = date(row.start)
MERGE (p)-[:HAS_COVERAGE]->(c)
"""

Q_CLAIMS = """
UNWIND $rows AS row
MATCH (p:Patient {id: row.patient_id})
MERGE (cl:Claim {id: row.claim_id})
SET cl.created = date(row.created), cl.totalInr = toFloat(row.total_inr),
    cl.encounterClass = row.encounter_class, cl.category = row.category,
    cl.planCode = row.plan_code, cl.diagnosis = row.dx_display, cl.procedure = row.procedure,
    // Historical insurer outcome: a legitimate input (the insurer told us).
    cl.denied = toInteger(row.denied), cl.denialReason = row.denial_reason,
    cl._truth_ring = row.ring_id
MERGE (p)-[:FILED]->(cl)
WITH cl, row, p
OPTIONAL MATCH (e:Encounter {id: row.encounter_id})
OPTIONAL MATCH (o:Organization {id: row.org_id})
OPTIONAL MATCH (d:Practitioner {id: row.practitioner_id})
OPTIONAL MATCH (cv:Coverage {id: row.coverage_id})
FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END | MERGE (cl)-[:FOR_ENCOUNTER]->(e))
FOREACH (_ IN CASE WHEN o IS NULL THEN [] ELSE [1] END | MERGE (cl)-[:BILLED_BY]->(o))
FOREACH (_ IN CASE WHEN d IS NULL THEN [] ELSE [1] END | MERGE (cl)-[:RENDERED_BY]->(d))
FOREACH (_ IN CASE WHEN cv IS NULL THEN [] ELSE [1] END | MERGE (cl)-[:UNDER_COVERAGE]->(cv))
WITH cl, row, p
// Link the claim to the patient's condition(s) with the primary diagnosis code.
OPTIONAL MATCH (p)-[:HAS_CONDITION]->(cd:Condition {code: row.dx_code})
FOREACH (_ IN CASE WHEN cd IS NULL THEN [] ELSE [1] END | MERGE (cl)-[:HAS_DIAGNOSIS]->(cd))
"""

# --- Derived relationships (run once after the base load) --------------------
DERIVED = {
    "Patient-[:VISITED]->Organization": """
        MATCH (p:Patient)-[:HAD_ENCOUNTER]->(e:Encounter)-[:AT]->(o:Organization)
        WITH p, o, count(e) AS n
        MERGE (p)-[r:VISITED]->(o) SET r.visits = n, r.weight = toFloat(n)
    """,
    "Patient-[:SEEN_BY]->Practitioner": """
        MATCH (p:Patient)-[:HAD_ENCOUNTER]->(e:Encounter)-[:ATTENDED_BY]->(d:Practitioner)
        WITH p, d, count(e) AS n
        MERGE (p)-[r:SEEN_BY]->(d) SET r.visits = n, r.weight = toFloat(n)
    """,
    "Practitioner-[:WORKS_AT]->Organization": """
        // Affiliation is inferred from where a doctor actually treats patients,
        // not from a declared roster: fraudsters rarely declare their moonlighting.
        MATCH (d:Practitioner)<-[:ATTENDED_BY]-(e:Encounter)-[:AT]->(o:Organization)
        WITH d, o, count(e) AS n
        MERGE (d)-[r:WORKS_AT]->(o) SET r.encounters = n, r.weight = toFloat(n)
    """,
    "Practitioner-[:REFERRED_TO]->Practitioner": """
        // Direction matters: referrer -> the doctor who then saw the patient.
        // A directed cycle in this relationship is a kickback-loop signature.
        MATCH (ref:Practitioner)<-[:REFERRED_BY]-(e:Encounter)-[:ATTENDED_BY]->(d:Practitioner)
        WHERE ref <> d
        WITH ref, d, count(e) AS n
        MERGE (ref)-[r:REFERRED_TO]->(d) SET r.count = n, r.weight = toFloat(n)
    """,
}


def reset() -> None:
    with get_driver().session() as s:
        # IN TRANSACTIONS deletes in chunks so a big graph doesn't blow the heap.
        s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()
    print("  database wiped")


def load() -> None:
    p = PATHS.processed
    claims_file = p / "claims_labeled.csv"
    if not claims_file.exists():
        raise SystemExit("Run `python -m data.simulate_adjudication` first (needs claims_labeled.csv).")

    with get_driver().session() as s:
        for label, key in CONSTRAINTS:
            s.run(f"CREATE CONSTRAINT {label.lower()}_{key} IF NOT EXISTS "
                  f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE").consume()

    patients = pd.read_csv(p / "patients.csv", dtype={"phone": str})
    patients["phone"] = patients["phone"].fillna("")
    patients["address_key"] = patients["address_key"].fillna("")
    claims = pd.read_csv(claims_file, dtype={"dx_code": str})
    conditions = pd.read_csv(p / "conditions.csv", dtype={"code": str})

    # Order matters: nodes that others point to must exist first.
    _write(Q_ORGS, _rows(pd.read_csv(p / "organizations.csv")), "Organization")
    _write(Q_PRACTS, _rows(pd.read_csv(p / "practitioners.csv", dtype=str)), "Practitioner")
    _write(Q_PATIENTS, _rows(patients), "Patient + Address + Phone")
    _write(Q_COVERAGES, _rows(pd.read_csv(p / "coverages.csv")), "Coverage")
    _write(Q_ENCOUNTERS, _rows(pd.read_csv(p / "encounters.csv")), "Encounter")
    _write(Q_CONDITIONS, _rows(conditions.dropna(subset=["onset"])), "Condition")
    _write(Q_CLAIMS, _rows(claims), "Claim")

    with get_driver().session() as s:
        for label, q in DERIVED.items():
            t = time.time()
            s.run(q).consume()
            print(f"  derived {label:<38} ({time.time() - t:.1f}s)")


def summary() -> None:
    with get_driver().session() as s:
        for rec in s.run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC"):
            print(f"  {rec['label']:<14} {rec['n']:>8,}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Load processed FHIR tables into Neo4j.")
    ap.add_argument("--reset", action="store_true", help="delete all nodes first")
    args = ap.parse_args()
    if not neo4j_available():
        raise SystemExit("Neo4j is not reachable. Start it: `docker compose up -d neo4j` and check .env.")
    if args.reset:
        reset()
    load()
    print("Node counts:")
    summary()
