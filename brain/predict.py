"""
brain/predict.py — serve the trained models: scores + explanations.

    from brain.predict import get_brain
    brain = get_brain()
    brain.denial(claim_id)   # risk, top reason codes, SHAP drivers
    brain.fraud(claim_id)    # fraud probability, SHAP drivers
    brain.scores()           # DataFrame of both scores for every claim (for lists)

    python -m brain.predict <claim_id>     # CLI demo

Explanations use XGBoost's built-in TreeSHAP (`pred_contribs=True`): for each
claim, each feature gets a signed contribution in log-odds. Positive pushes
toward denial/fraud. The contributions plus the bias sum exactly to the
model's raw score, so the explanation is faithful to the model and not an
approximation from a second model.
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache

import numpy as np
import pandas as pd
import xgboost as xgb

from brain.features import FEATURE_TEXT, denial_matrix, fraud_matrix, load_claim_frame
from core.config import PATHS
from data.reference import REASON_CODES

# Which feature points to which reason code. Used to label SHAP drivers so the
# UI can say "pre-auth missing → D01" next to the model's reason ranking.
FEATURE_REASON_HINT = {
    "preauth_missing": "D01", "preauth_obtained": "D01", "requires_preauth": "D01",
    "outpatient_on_hospital_only_plan": "D02", "category_excluded_by_plan": "D02",
    "onset_before_cover": "D03", "ped_window_open": "D03", "months_since_cover": "D03", "chronic": "D03",
    "docs_complete": "D04", "n_documents": "D04",
    "amount_to_sublimit": "D05",
    "days_to_submit": "D06",
    "ring_score": "D07", "p_claims_90d": "D07",
}


class Brain:
    def __init__(self) -> None:
        meta_path = PATHS.models / "metadata.json"
        if not meta_path.exists():
            raise RuntimeError("No trained models. Run `python -m brain.train`.")
        self.meta = json.loads(meta_path.read_text())
        self.models: dict[str, xgb.XGBClassifier] = {}
        for name in ("denial_model", "reason_model", "fraud_model"):
            m = xgb.XGBClassifier()
            m.load_model(PATHS.models / f"{name}.json")
            self.models[name] = m
        self.reason_classes: list[str] = self.meta["models"]["reason_model"]["classes"]

        # Precompute matrices for every known claim once; per-claim calls are lookups.
        self.df = load_claim_frame().set_index("claim_id", drop=False)
        self.Xd = denial_matrix(self.df)[self.meta["models"]["denial_model"]["features"]]
        self.Xf = fraud_matrix(self.df)[self.meta["models"]["fraud_model"]["features"]]

    # -- versions for the audit log -----------------------------------------
    def version(self, name: str) -> str:
        return self.meta["models"][name]["version"]

    # -- batch scores ---------------------------------------------------------
    @lru_cache(maxsize=1)
    def scores(self) -> pd.DataFrame:
        out = pd.DataFrame(index=self.df.index)
        out["denial_risk"] = self.models["denial_model"].predict_proba(self.Xd)[:, 1]
        out["fraud_score"] = self.models["fraud_model"].predict_proba(self.Xf)[:, 1]
        return out.astype("float64").round(4)

    # -- explanations ---------------------------------------------------------
    @staticmethod
    def _drivers(model: xgb.XGBClassifier, x: pd.DataFrame, k: int = 6) -> list[dict]:
        contrib = model.get_booster().predict(xgb.DMatrix(x), pred_contribs=True)[0][:-1]  # last col = bias
        order = np.argsort(-np.abs(contrib))[:k]
        return [{
            "feature": x.columns[i],
            "description": FEATURE_TEXT.get(x.columns[i], x.columns[i]),
            "value": round(float(x.iloc[0, i]), 4),
            "contribution": round(float(contrib[i]), 4),
            "direction": "raises" if contrib[i] > 0 else "lowers",
            "hint_reason": FEATURE_REASON_HINT.get(x.columns[i]),
        } for i in order]

    def _require(self, claim_id: str) -> None:
        if claim_id not in self.df.index:
            raise KeyError(f"Unknown claim_id {claim_id}")

    def denial(self, claim_id: str) -> dict:
        self._require(claim_id)
        x = self.Xd.loc[[claim_id]]
        risk = float(self.models["denial_model"].predict_proba(x)[0, 1])
        p_reason = self.models["reason_model"].predict_proba(x)[0]
        reasons = sorted(({"code": c, "label": REASON_CODES[c],
                           "p_given_denied": round(float(p), 4),
                           "probability": round(float(p) * risk, 4)}
                          for c, p in zip(self.reason_classes, p_reason)),
                         key=lambda r: -r["probability"])
        row = self.df.loc[claim_id]
        return {
            "claim_id": claim_id,
            "denial_risk": round(risk, 4),
            "risk_band": "HIGH" if risk >= 0.6 else "MEDIUM" if risk >= 0.3 else "LOW",
            "top_reasons": reasons[:3],
            "drivers": self._drivers(self.models["denial_model"], x),
            # Historical outcome, if the insurer already decided this claim (synthetic label).
            "actual": {"denied": int(row["denied"]), "reason": row["denial_reason"],
                       "wrongful": int(row["denial_wrongful"])},
            "model_version": self.version("denial_model"),
            "reason_model_version": self.version("reason_model"),
        }

    def fraud(self, claim_id: str) -> dict:
        self._require(claim_id)
        x = self.Xf.loc[[claim_id]]
        score = float(self.models["fraud_model"].predict_proba(x)[0, 1])
        return {
            "claim_id": claim_id,
            "fraud_score": round(score, 4),
            "community": int(self.df.loc[claim_id, "community"]),
            "ring_score": float(self.df.loc[claim_id, "ring_score"]),
            "drivers": self._drivers(self.models["fraud_model"], x),
            "model_version": self.version("fraud_model"),
        }

    def claim_row(self, claim_id: str) -> dict:
        self._require(claim_id)
        r = self.df.loc[claim_id]
        return {k: (None if (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v))
                for k, v in r.items()}


@lru_cache(maxsize=1)
def get_brain() -> Brain:
    return Brain()


if __name__ == "__main__":
    b = get_brain()
    cid = sys.argv[1] if len(sys.argv) > 1 else b.scores().sort_values("denial_risk").index[-1]
    print(json.dumps({"denial": b.denial(cid), "fraud": b.fraud(cid)}, indent=2, default=str))
