"""
scripts/build_demo_snapshot.py — pre-generate everything the static demo shows.

    python scripts/build_demo_snapshot.py            # needs the pipeline + GROQ_API_KEY
    DEMO_AUDIT_DB=/path/demo_audit.db python scripts/build_demo_snapshot.py --resume
        # keep files already generated (e.g. after Groq rate limits stalled a run)

GitHub Pages can only serve files, so the public demo can't run Python, Neo4j
or an LLM. This script calls the REAL FastAPI app in-process (TestClient) and
saves each response as JSON under ui/public/demo/. The demo UI (lib/api.ts,
NEXT_PUBLIC_DEMO=1) then answers every call from those files. So everything a
visitor sees is genuine output of this pipeline, just captured ahead of time.

What gets captured:
  * claim lists for each view + a denial explanation for every listed claim
  * fraud rings
  * pre-auth drafts (LLM) for the top patients
  * appeal letters (LLM) for the wrongfully-denied claims shown in the picker
  * a pipeline run for every listed claim whose route only needs the cached
    LLM results above (others show "not in demo snapshot")
  * the audit log those calls produced, in a throwaway DB (your real log is untouched)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Must be set before project modules are imported (config reads env at import).
os.environ["AUDIT_DB_PATH"] = os.getenv("DEMO_AUDIT_DB") or str(Path(tempfile.mkdtemp()) / "demo_audit.db")
RESUME = "--resume" in sys.argv

from fastapi.testclient import TestClient  # noqa: E402

import api.orchestrator as orch  # noqa: E402
from api.main import app  # noqa: E402
from core.llm import get_llm  # noqa: E402

OUT = ROOT / "ui" / "public" / "demo"
VIEWS = ["all", "high_risk", "fraud", "denied", "wrongful"]
N_APPEALS, N_PREAUTH = 25, 10
client = TestClient(app, raise_server_exceptions=False)
# Skipped (not-cached) pipeline runs are expected here; don't log their tracebacks.
logging.getLogger("api").setLevel(logging.CRITICAL)


def save(name: str, data) -> None:
    path = OUT / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), default=str))


def llm_call(method: str, url: str, body: dict, attempts: int = 4) -> dict | None:
    """Call an LLM-backed endpoint; insist the result came from the real model,
    not the template fallback (which happens if Groq rate-limits us)."""
    for i in range(attempts):
        r = client.request(method, url, json=body)
        data = r.json() if r.status_code == 200 else {}
        if data and data.get("generated_by", "template") != "template":
            return data
        wait = 20 * (i + 1)
        print(f"    retry in {wait}s ({r.status_code} {data.get('generated_by', data.get('detail', ''))!s:.60})")
        time.sleep(wait)
    return None


class _NotCached(Exception):
    pass


def main() -> None:
    llm = get_llm()
    if llm.is_mock:
        raise SystemExit("Set a real LLM_PROVIDER (e.g. groq) in .env: the demo should show real letters.")
    print("audit DB:", os.environ["AUDIT_DB_PATH"])  # pass as DEMO_AUDIT_DB to --resume
    if OUT.exists() and not RESUME:
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    have = lambda name: RESUME and (OUT / f"{name}.json").exists()  # noqa: E731
    t0 = time.time()

    save("health", client.get("/api/health").json())

    # Claims per view + denial explanations (no LLM).
    ids: list[str] = []
    for v in VIEWS:
        data = client.get(f"/api/claims?view={v}&limit=150").json()
        save(f"claims_{v}", data)
        ids += [c["claim_id"] for c in data["claims"] if c["claim_id"] not in ids]
    for cid in ids:
        if not have(f"denial/{cid}"):
            save(f"denial/{cid}", client.get(f"/api/denial/{cid}").json())
    print(f"claims + {len(ids)} denial explanations ({time.time() - t0:.0f}s)")

    save("rings", client.get("/api/fraud/rings").json())

    # Pre-auth drafts (LLM).
    patients = client.get("/api/patients?limit=40").json()[:N_PREAUTH]
    kept, preauth_cache = [], {}
    for p in patients:
        pid = p["patient_id"]
        if have(f"preauth/{pid}"):
            preauth_cache[pid] = json.loads((OUT / f"preauth/{pid}.json").read_text())
            kept.append(p)
            continue
        print(f"  pre-auth {p['name']}")
        d = llm_call("POST", "/api/preauth", {"patient_id": pid})
        if d:
            preauth_cache[pid] = d
            save(f"preauth/{pid}", d)
            save(f"encounters/{pid}", client.get(f"/api/patients/{pid}/encounters").json())
            kept.append(p)
        time.sleep(2)
    save("patients", kept)
    print(f"{len(kept)} pre-auth drafts ({time.time() - t0:.0f}s)")

    # Appeals (LLM) for the claims the Appeal page's picker shows first.
    wrongful = json.loads((OUT / "claims_wrongful.json").read_text())["claims"][:N_APPEALS]
    appeal_cache = {}
    for c in wrongful:
        if (OUT / f"appeal/{c['claim_id']}.json").exists() and RESUME:
            appeal_cache[c["claim_id"]] = json.loads((OUT / f"appeal/{c['claim_id']}.json").read_text())
            continue
        if RESUME:
            continue  # don't retry letters that stalled the first run
        print(f"  appeal {c['claim_id'][:8]} {c['name']}")
        d = llm_call("POST", "/api/appeal", {"claim_id": c["claim_id"]})
        if d:
            appeal_cache[c["claim_id"]] = d
            save(f"appeal/{c['claim_id']}", d)
        time.sleep(3)
    print(f"{len(appeal_cache)} appeals ({time.time() - t0:.0f}s)")

    # Pipeline runs: reuse the cached LLM outputs; skip claims whose route
    # would need a letter/draft we didn't pre-generate.
    def cached_appeal(cid):
        if cid not in appeal_cache:
            raise _NotCached(cid)
        return appeal_cache[cid]

    def cached_preauth(pid, eid=None):
        if pid not in preauth_cache:
            raise _NotCached(pid)
        return preauth_cache[pid]

    orch.run_appeal, orch.run_preauth = cached_appeal, cached_preauth
    done = 0
    for cid in ids:
        r = client.post(f"/api/process/{cid}")
        if r.status_code == 200:
            save(f"process/{cid}", r.json())
            done += 1
    print(f"{done}/{len(ids)} pipeline runs ({time.time() - t0:.0f}s)")

    save("audit", client.get("/api/audit?limit=1000").json())
    save("audit_verify", client.get("/api/audit/verify").json())
    size = sum(f.stat().st_size for f in OUT.rglob("*.json")) / 1e6
    print(f"Snapshot: {sum(1 for _ in OUT.rglob('*.json'))} files, {size:.1f} MB -> {OUT}")


if __name__ == "__main__":
    main()
