"""
brain/features.py — turn claims (+ graph features) into model-ready matrices.

Two feature sets, deliberately different:

DENIAL features: what an insurer's adjudication engine looks at.
  Amount vs thresholds/sub-limits, pre-auth status, documents, filing delay,
  chronic disease and timing vs policy start, plan, category, encounter type.
  We give the model the RAW INPUTS of the rules, not the rule outcomes, so it
  has to learn the insurer's behaviour, including its leniency and its
  pre-existing-disease bug, from labels. (If we handed it "rule D03 fired",
  it would learn the rulebook and miss the bug.)

FRAUD features: what a ring looks like.
  The graph features from graph/graph_analytics.py (shared identities,
  doctor-clinic mesh, referral loops, threshold structuring, community ring
  score) plus a few claim fields. The insurer's denial outcome is EXCLUDED: it
  is partly caused by fraud (D07), so using it would leak the label.

`CLAIM_ONLY_FRAUD_FEATURES` is a baseline without graph features. train.py
fits both and prints the AUC difference, which is the measurable value the
graph adds.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from core.config import PATHS
from data.reference import CATEGORIES, PLAN_CODES, PLANS

ENCOUNTER_CLASSES = ["AMB", "IMP", "EMER"]  # anything else -> "other"

GRAPH_FEATURES = [
    "p_shared_phone", "p_shared_address", "p_distinct_clinics", "p_claims_90d",
    "d_clinic_count", "d_near_threshold_rate", "d_in_referral_loop",
    "o_shared_doctor_clinics", "o_max_patient_jaccard", "o_pagerank",
    "comm_patients", "comm_identity_rate", "comm_velocity_rate", "comm_doc_mesh_rate",
    "comm_referral_loop_rate", "comm_structuring_rate", "ring_score",
]

# Plain-English description of every feature; the UI shows these next to the
# SHAP contribution so a claims officer can read *why*.
FEATURE_TEXT = {
    "log_amount": "claimed amount",
    "amount_to_preauth_threshold": "amount relative to pre-auth threshold",
    "amount_to_sublimit": "amount relative to category sub-limit",
    "requires_preauth": "pre-authorisation required",
    "preauth_obtained": "pre-authorisation obtained",
    "preauth_missing": "pre-auth required but missing",
    "docs_complete": "documents complete",
    "n_documents": "number of documents attached",
    "days_to_submit": "days taken to submit",
    "chronic": "chronic condition",
    "onset_before_cover": "condition diagnosed before policy start",
    "months_since_cover": "months since policy start",
    "ped_window_open": "still inside pre-existing-disease waiting window",
    "outpatient_on_hospital_only_plan": "outpatient care on hospitalisation-only plan",
    "category_excluded_by_plan": "category excluded by plan",
    "patient_age": "patient age",
    **{f"enc_{c}": f"encounter type {c}" for c in ENCOUNTER_CLASSES + ["other"]},
    **{f"cat_{c}": f"category {c}" for c in CATEGORIES},
    **{f"plan_{p}": f"plan {p}" for p in PLAN_CODES},
    "p_shared_phone": "other patients sharing this phone",
    "p_shared_address": "other patients sharing this address",
    "p_distinct_clinics": "distinct clinics the patient visited",
    "p_claims_90d": "patient's claims in last 90 days",
    "d_clinic_count": "clinics the doctor bills at",
    "d_near_threshold_rate": "doctor's share of claims just below pre-auth threshold",
    "d_in_referral_loop": "doctor is in a closed referral loop",
    "o_shared_doctor_clinics": "clinics sharing 2+ doctors with this clinic",
    "o_max_patient_jaccard": "patient overlap with another clinic",
    "o_pagerank": "clinic centrality (PageRank)",
    "comm_patients": "size of graph community",
    "comm_identity_rate": "identity sharing in community",
    "comm_velocity_rate": "high-velocity claimants in community",
    "comm_doc_mesh_rate": "multi-clinic doctors in community",
    "comm_referral_loop_rate": "referral-loop doctors in community",
    "comm_structuring_rate": "threshold-structured claims in community",
    "ring_score": "community ring score",
}


def _to_date(s) -> date | None:
    if pd.isna(s) or s == "":
        return None
    return date.fromisoformat(str(s)[:10])


def load_claim_frame() -> pd.DataFrame:
    """claims_labeled ⨝ graph_features ⨝ patient age. One row per claim."""
    p = PATHS.processed
    claims = pd.read_csv(p / "claims_labeled.csv", dtype={"dx_code": str})
    gf = pd.read_csv(p / "graph_features.csv")
    pats = pd.read_csv(p / "patients.csv")[["patient_id", "birth_date", "name"]]
    df = claims.merge(gf, on="claim_id", how="left").merge(pats, on="patient_id", how="left")
    df[GRAPH_FEATURES] = df[GRAPH_FEATURES].fillna(0)
    return df


def _one_hot(series: pd.Series, values: list[str], prefix: str) -> pd.DataFrame:
    # Fixed vocabularies (not pd.get_dummies) so train-time and serve-time
    # matrices always have identical columns, even for a single claim.
    return pd.DataFrame({f"{prefix}_{v}": (series == v).astype(int) for v in values}, index=series.index)


def denial_matrix(df: pd.DataFrame) -> pd.DataFrame:
    plan = df["plan_code"].map(PLANS)
    thr = plan.map(lambda p: p.preauth_threshold_inr)
    sub = [PLANS[pc].sublimits_inr.get(cat) for pc, cat in zip(df["plan_code"], df["category"])]
    created = df["created"].map(_to_date)
    onset = df["dx_onset"].map(_to_date)
    cov_start = df["coverage_start"].map(_to_date)
    enc = df["encounter_class"].where(df["encounter_class"].isin(ENCOUNTER_CLASSES), "other")

    X = pd.DataFrame(index=df.index)
    X["log_amount"] = np.log1p(df["total_inr"])
    X["amount_to_preauth_threshold"] = df["total_inr"] / thr
    X["amount_to_sublimit"] = [a / s if s else 0.0 for a, s in zip(df["total_inr"], sub)]
    X["requires_preauth"] = df["requires_preauth"]
    X["preauth_obtained"] = df["preauth_obtained"]
    X["preauth_missing"] = ((df["requires_preauth"] == 1) & (df["preauth_obtained"] == 0)).astype(int)
    X["docs_complete"] = df["docs_complete"]
    X["n_documents"] = df["n_documents"]
    X["days_to_submit"] = df["days_to_submit"]
    X["chronic"] = df["chronic"]
    X["onset_before_cover"] = [int(o is not None and c is not None and o < c) for o, c in zip(onset, cov_start)]
    X["months_since_cover"] = df["months_since_cover"]
    X["ped_window_open"] = (df["months_since_cover"] < plan.map(lambda p: p.ped_waiting_months)).astype(int)
    X["outpatient_on_hospital_only_plan"] = [
        int(not PLANS[pc].opd_covered and e == "AMB" and cat not in PLANS[pc].daycare_categories)
        for pc, e, cat in zip(df["plan_code"], df["encounter_class"], df["category"])]
    X["category_excluded_by_plan"] = [int(cat in PLANS[pc].excluded_categories)
                                      for pc, cat in zip(df["plan_code"], df["category"])]
    X["patient_age"] = [(c - _to_date(b)).days / 365.25 if c and isinstance(b, str) else 40.0
                        for c, b in zip(created, df["birth_date"])]
    X = pd.concat([X,
                   _one_hot(enc, ENCOUNTER_CLASSES + ["other"], "enc"),
                   _one_hot(df["category"], CATEGORIES, "cat"),
                   _one_hot(df["plan_code"], PLAN_CODES, "plan"),
                   # Graph context: lets the denial model anticipate SIU (D07) denials.
                   df[["p_claims_90d", "ring_score"]]], axis=1)
    return X.astype(float)


CLAIM_ONLY_FRAUD_FEATURES = ["log_amount", "amount_to_preauth_threshold", "patient_age"] + \
    [f"enc_{c}" for c in ENCOUNTER_CLASSES + ["other"]] + [f"cat_{c}" for c in CATEGORIES]


def fraud_matrix(df: pd.DataFrame, with_graph: bool = True) -> pd.DataFrame:
    base = denial_matrix(df)[CLAIM_ONLY_FRAUD_FEATURES]
    if not with_graph:
        return base
    return pd.concat([base, df[GRAPH_FEATURES].astype(float)], axis=1)
