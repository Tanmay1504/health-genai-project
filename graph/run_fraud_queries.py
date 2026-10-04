"""
graph/run_fraud_queries.py — execute the named queries in fraud_queries.cypher.

    python -m graph.run_fraud_queries                 # run every pattern query
    python -m graph.run_fraud_queries referral_cycles # run one

The .cypher file is the single source of truth. This module only parses it
into {name: query} and supplies default parameters.
"""
from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path

from data.reference import PLANS
from graph.db import neo4j_available, run

CYPHER_FILE = Path(__file__).with_name("fraud_queries.cypher")

# Defaults for the $parameters used in the file.
DEFAULT_PARAMS = {
    "shared_phone_clusters": {"min_patients": 4},
    "shared_address_clusters": {"min_patients": 5},
    "threshold_structuring": {"thresholds": {code: p.preauth_threshold_inr for code, p in PLANS.items()}},
}

# The "pattern" queries detect fraud; the gds_* ones are analytics plumbing.
PATTERN_QUERIES = ["shared_phone_clusters", "shared_address_clusters", "clinic_doctor_mesh",
                   "referral_cycles", "claim_velocity", "threshold_structuring"]


@lru_cache(maxsize=1)
def load_queries() -> dict[str, str]:
    """Split the file on `// name:` markers. Comment lines are kept: Cypher
    ignores them, and they make query logs readable."""
    text = CYPHER_FILE.read_text()
    out: dict[str, str] = {}
    for block in re.split(r"(?m)^// name:\s*", text)[1:]:
        name, _, body = block.partition("\n")
        out[name.strip()] = body.strip().rstrip(";").strip()
    return out


def run_named(name: str, **params) -> list[dict]:
    q = load_queries()[name]
    return run(q, **{**DEFAULT_PARAMS.get(name, {}), **params})


def run_all_patterns() -> dict[str, list[dict]]:
    return {name: run_named(name) for name in PATTERN_QUERIES}


def _print(name: str, rows: list[dict]) -> None:
    print(f"\n=== {name}  ({len(rows)} hits) " + "=" * max(0, 50 - len(name)))
    for r in rows[:10]:
        printable = {k: (v if not (isinstance(v, list) and len(v) > 6) else v[:6] + ["..."]) for k, v in r.items()}
        print("  ", printable)


if __name__ == "__main__":
    if not neo4j_available():
        raise SystemExit("Neo4j is not reachable. `docker compose up -d neo4j`, then `python -m graph.load_graph`.")
    names = sys.argv[1:] or PATTERN_QUERIES
    for n in names:
        _print(n, run_named(n))
