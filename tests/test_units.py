"""Fast unit tests: no data pipeline needed."""
import sqlite3
from datetime import date

from core.config import PATHS
from core.llm import extract_json, get_llm
from data.parse_fhir import RefIndex
from data.reference import category_for, evaluate_policy, is_chronic
from trust import audit


def _claim(**kw):
    base = dict(plan_code="SGH-SILVER", category="diabetes_metabolic", encounter_class="AMB", total_inr=2000,
                preauth_obtained=False, chronic=True, dx_onset=date(2020, 1, 1), coverage_start=date(2024, 1, 1),
                claim_date=date(2025, 1, 1), docs_complete=True, days_to_submit=5)
    base.update(kw)
    return base


# ------------------------------------------------------------------ rule engine
def test_ped_applies_when_diagnosed_before_cover():
    codes = [v["code"] for v in evaluate_policy(_claim())]
    assert codes == ["D03"]


def test_ped_does_not_apply_when_diagnosed_after_cover():
    # The insurer "bug" the appeal agent exploits: chronic but NOT pre-existing.
    assert evaluate_policy(_claim(dx_onset=date(2024, 6, 1))) == []


def test_ped_window_completed():
    assert evaluate_policy(_claim(claim_date=date(2027, 2, 1))) == []  # 37 months > 36


def test_sublimit_and_preauth():
    codes = {v["code"] for v in evaluate_policy(_claim(category="ophthalmology", chronic=False, total_inr=40000))}
    assert codes == {"D05", "D01"}


def test_hospital_only_plan_rejects_opd_consult():
    codes = [v["code"] for v in evaluate_policy(_claim(plan_code="JAC-BASIC", category="respiratory", chronic=False))]
    assert codes == ["D02"]


def test_category_mapping_order():
    assert category_for("Diabetic retinopathy") == "ophthalmology"   # specific organ beats generic "diabet"
    assert category_for("Diabetes mellitus type 2") == "diabetes_metabolic"
    assert is_chronic("Essential hypertension") and not is_chronic("Acute bronchitis")
    assert category_for(float("nan")) == "general_medicine" and not is_chronic(None)  # Synthea: no diagnosis


# ------------------------------------------------------------------ FHIR refs
def test_ref_index_resolves_all_three_styles():
    idx = RefIndex()
    idx.add({"resourceType": "Practitioner", "id": "abc", "identifier": [{"system": "s", "value": "999"}]})
    assert idx.resolve("urn:uuid:x1") == (None, "x1")
    assert idx.resolve("Practitioner?identifier=s|999") == ("Practitioner", "abc")
    assert idx.resolve("Organization/o9") == ("Organization", "o9")


# ------------------------------------------------------------------ LLM wrapper
def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"a": [1,2]} hope that helps') == {"a": [1, 2]}
    assert extract_json("no json here") is None


def test_fullwidth_citations_are_normalised():
    from core.llm import normalize_citations
    assert normalize_citations("see 【suraksha_silver_policy#5】 and 【x#2†L1-L4】") == "see [suraksha_silver_policy#5] and [x#2]"


def test_mock_provider_falls_back_to_default():
    llm = get_llm()
    assert llm.is_mock
    assert llm.complete_json("sys", "user", default={"x": 1}) == {"x": 1}


# ------------------------------------------------------------------ audit chain
def test_audit_chain_detects_tampering():
    for i in range(3):
        audit.record_decision(module="test", subject_id=f"c{i}", recommendation="OK", inputs={"i": i}, output={})
    assert audit.verify_chain()["valid"]
    con = sqlite3.connect(PATHS.audit_db)
    con.execute("UPDATE decisions SET recommendation = 'TAMPERED' WHERE subject_id = 'c1'")
    con.commit()
    con.close()
    res = audit.verify_chain()
    assert not res["valid"] and res["first_broken_seq"] >= 2


def test_groq_uses_local_embeddings_by_default(monkeypatch):
    from core.config import Settings
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "")
    assert Settings().resolved_embeddings_provider() == "local"
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "mock")
    assert Settings().resolved_embeddings_provider() == "mock"
