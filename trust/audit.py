"""
trust/audit.py — the traceable decision record ("why did the system say that?").

Every module (pre-auth, fraud, denial, appeal, orchestrator) calls
`record_decision(...)` once per decision. Each record stores:

    WHAT was decided     recommendation + full structured output
    ON WHAT              the inputs (claim/patient fields actually used)
    BY WHAT              model name + version (from brain/models/metadata.json)
                         and LLM provider + model (from core/llm.py)
    FROM WHAT EVIDENCE   retrieval context: chunk ids + text the LLM was given
    WHEN                 UTC timestamp

Tamper evidence: a HASH CHAIN
  Each row stores `record_hash = SHA-256(prev_hash + canonical JSON of the row)`.
  Editing or deleting any past row breaks every hash after it, and
  `verify_chain()` reports the first broken sequence number. It is the same
  idea as a blockchain or git history, without the distributed part. SQLite
  is fine here: it is one file, transactional, and needs no server. For production
  you would ship these rows to WORM storage (write-once-read-many).

Run:  python -m trust.audit --verify      (check the chain)
      python -m trust.audit --tail 5      (print latest records)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from core.config import PATHS

GENESIS = "0" * 64  # prev_hash of the very first record

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     TEXT UNIQUE NOT NULL,
    created_at      TEXT NOT NULL,
    module          TEXT NOT NULL,       -- preauth | fraud | denial | appeal | orchestrator
    subject_type    TEXT NOT NULL,       -- claim | patient
    subject_id      TEXT NOT NULL,
    recommendation  TEXT NOT NULL,
    model_name      TEXT,
    model_version   TEXT,
    llm_provider    TEXT,
    llm_model       TEXT,
    inputs_json     TEXT NOT NULL,
    retrieval_json  TEXT NOT NULL,
    output_json     TEXT NOT NULL,
    prev_hash       TEXT NOT NULL,
    record_hash     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_subject ON decisions(subject_id);
CREATE INDEX IF NOT EXISTS idx_decisions_module ON decisions(module);
"""

# Columns covered by the hash, in a fixed order.
HASHED = ["decision_id", "created_at", "module", "subject_type", "subject_id", "recommendation",
          "model_name", "model_version", "llm_provider", "llm_model",
          "inputs_json", "retrieval_json", "output_json"]


def _canonical(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace variance, dates as str."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def _hash(prev_hash: str, row: dict) -> str:
    payload = prev_hash + _canonical({k: row.get(k) for k in HASHED})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@contextmanager
def _conn():
    PATHS.audit_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(PATHS.audit_db, timeout=30, isolation_level=None)  # manual transactions
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        yield con
    finally:
        con.close()


def record_decision(*, module: str, subject_id: str, recommendation: str, inputs: dict, output: dict,
                    subject_type: str = "claim", model_name: str | None = None,
                    model_version: str | None = None, llm: dict | None = None,
                    retrieval: list[dict] | None = None) -> str:
    """Append one decision. Returns its decision_id (UUID).

    BEGIN IMMEDIATE takes the write lock *before* reading the last hash, so two
    concurrent API requests can't both chain onto the same predecessor.
    """
    llm = llm or {}
    row = {
        "decision_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "module": module, "subject_type": subject_type, "subject_id": subject_id,
        "recommendation": recommendation, "model_name": model_name, "model_version": model_version,
        "llm_provider": llm.get("llm_provider"), "llm_model": llm.get("llm_model"),
        "inputs_json": _canonical(inputs), "retrieval_json": _canonical(retrieval or []),
        "output_json": _canonical(output),
    }
    with _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            last = con.execute("SELECT record_hash FROM decisions ORDER BY seq DESC LIMIT 1").fetchone()
            row["prev_hash"] = last["record_hash"] if last else GENESIS
            row["record_hash"] = _hash(row["prev_hash"], row)
            cols = ", ".join(row)
            con.execute(f"INSERT INTO decisions ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return row["decision_id"]


def _decode(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("inputs_json", "retrieval_json", "output_json"):
        d[k.removesuffix("_json")] = json.loads(d.pop(k))
    return d


def list_decisions(limit: int = 100, module: str | None = None, subject_id: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM decisions WHERE 1=1", []
    if module:
        q, args = q + " AND module = ?", args + [module]
    if subject_id:
        q, args = q + " AND subject_id = ?", args + [subject_id]
    q += " ORDER BY seq DESC LIMIT ?"
    with _conn() as con:
        return [_decode(r) for r in con.execute(q, args + [limit])]


def get_decision(decision_id: str) -> dict | None:
    with _conn() as con:
        r = con.execute("SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        return _decode(r) if r else None


def verify_chain() -> dict:
    """Recompute every hash in order. Any edit/deletion/reorder is detected."""
    prev = GENESIS
    n = 0
    with _conn() as con:
        for r in con.execute("SELECT * FROM decisions ORDER BY seq ASC"):
            row = dict(r)
            if row["prev_hash"] != prev or _hash(prev, row) != row["record_hash"]:
                return {"valid": False, "records_checked": n, "first_broken_seq": row["seq"]}
            prev = row["record_hash"]
            n += 1
    return {"valid": True, "records_checked": n, "head_hash": prev}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Inspect the audit log.")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--tail", type=int, default=0)
    a = ap.parse_args()
    if a.verify:
        print(verify_chain())
    for d in list_decisions(limit=a.tail) if a.tail else []:
        print(f"#{d['seq']} {d['created_at']} {d['module']:<12} {d['subject_id'][:12]} -> {d['recommendation']} "
              f"[{d['model_version'] or '-'} | {d['llm_provider'] or '-'}]")
