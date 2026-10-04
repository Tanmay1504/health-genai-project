"""End-to-end smoke tests over the real (synthetic) pipeline outputs, offline.
Skipped automatically until the pipeline has been run once."""
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core.config import PATHS
from tests.conftest import needs_pipeline

pytestmark = needs_pipeline


@pytest.fixture(scope="module")
def claims():
    return pd.read_csv(PATHS.processed / "claims_labeled.csv")


@pytest.fixture(scope="module")
def client():
    from api.main import app
    return TestClient(app)


def test_rings_recover_planted_fraud():
    out = json.loads((PATHS.processed / "fraud_rings.json").read_text())
    ev = out["evaluation"]
    assert ev["patient_recall"] >= 0.9, ev


def test_denial_explanations(claims):
    from brain.predict import get_brain
    cid = claims[claims.denial_wrongful == 1].claim_id.iloc[0]
    d = get_brain().denial(cid)
    assert 0 <= d["denial_risk"] <= 1 and len(d["top_reasons"]) == 3 and d["drivers"]


def test_appeal_on_wrongful_ped_denial_is_strong_and_cited(claims):
    from appeal.agent import run_appeal
    cid = claims[(claims.denial_wrongful == 1) & (claims.denial_reason == "D03")].claim_id.iloc[0]
    r = run_appeal(cid)
    assert r["appeal_strength"] == "STRONG"
    assert r["citations"], "appeal must cite sources"
    assert any("pre-existing" in c["quote"].lower() for c in r["citations"])


def test_preauth_draft(claims):
    from preauth.agent import run_preauth
    pid = claims[claims.encounter_class == "IMP"].patient_id.iloc[0]
    r = run_preauth(pid)
    assert r["status"] == "DRAFT" and r["fhir_claim"]["use"] == "preauthorization"
    assert r["decision_id"]


def test_orchestrator_holds_fraud(claims):
    from api.orchestrator import process_claim
    cid = claims[claims.is_planted_fraud == 1].claim_id.iloc[0]
    assert process_claim(cid)["outcome"] == "HOLD_REFER_TO_SIU"


def test_api_endpoints(client, claims):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/claims?view=wrongful").json()["total"] > 0
    assert client.get("/api/denial/does-not-exist").status_code == 404
    assert client.get("/api/audit/verify").json()["valid"]


class _ScriptedChat:
    """Stands in for a real chat model so the LLM branches (not the templates)
    get exercised. First draft cites a non-existent source on purpose; the
    agent's verifier must catch it and the revision must fix it."""

    def invoke(self, messages):
        import re
        from langchain_core.messages import AIMessage
        prompt = messages[-1].content
        if "search queries" in prompt:
            return AIMessage(content='["pre-existing disease definition", "burden of proof waiting period"]')
        if "Candidate sources" in prompt:
            ids = re.findall(r"^\[([^\]]+#\d+)\]", prompt, flags=re.M)[:4]
            return AIMessage(content=json.dumps(ids + [{"junk": 1}, "invented#99"]))
        claim_id = re.search(r'"claim_id": "([^"]+)"', prompt).group(1)
        if "previous draft had these problems" not in prompt:
            return AIMessage(content=f"Appeal for claim {claim_id}.\n\n" + "The denial is wrong per the policy. " * 8 + "[invented#99]")
        first_src = re.search(r"^\[([^\]]+#\d+)\]", prompt.split("SOURCES")[1], flags=re.M).group(1)
        return AIMessage(content=f"Appeal for claim {claim_id}.\n\n" + "The denial is wrong per the policy. " * 8 + f"[{first_src}]")


def test_appeal_llm_path_catches_hallucinated_citation(claims, monkeypatch):
    import appeal.agent as ag
    from core.llm import LLMClient
    monkeypatch.setattr(ag, "get_llm", lambda: LLMClient("fake", "scripted", _ScriptedChat()))
    cid = claims[(claims.denial_wrongful == 1) & (claims.denial_reason == "D03")].claim_id.iloc[0]
    r = ag.run_appeal(cid)
    assert r["generated_by"] == "fake/scripted"
    assert r["trace"]["revisions"] == 1                # verifier rejected draft 1
    assert "invented#99" not in r["letter"]
    assert r["citations"] and all("#" in c["chunk_id"] for c in r["citations"])
