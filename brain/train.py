"""
brain/train.py — train the three models of the "brain".

    python -m brain.train

  1. denial_model  (XGBoost, binary)      P(claim will be denied)
  2. reason_model  (XGBoost, multiclass)  P(reason | denied), on denied claims only
  3. fraud_model   (XGBoost, binary)      P(claim belongs to a fraud ring)

WHY two models for denial instead of one 8-class model?
  The business question has two parts: "will it be denied?" (a risk score
  to rank the work queue) and "why?" (which fix or appeal to prepare).
  P(denied) is learned from all claims, while P(reason | denied) is learned
  only from denials. Each gets the data most relevant to it. Combined:
      P(reason r) = P(denied) × P(r | denied)

WHY XGBoost?
  Tabular data with interactions ("chronic AND inside waiting window AND plan
  = Silver") is exactly what gradient-boosted trees learn well. XGBoost also
  ships exact TreeSHAP (`pred_contribs=True`), which gives per-claim
  explanations with no extra library.

Class imbalance: fraud is ~6% of claims, so `scale_pos_weight` up-weights
positives. Otherwise the model can reach 94% accuracy by never predicting
fraud. We report PR-AUC next to ROC-AUC because PR-AUC is honest under
imbalance.

Artefacts -> brain/models/: <name>.json (XGBoost native format, no pickle)
+ metadata.json (version, features, classes, metrics). The `version` string is
written into every audit record, so a decision can be traced to the exact
model that made it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, average_precision_score, f1_score, roc_auc_score
from sklearn.model_selection import train_test_split

from brain.features import denial_matrix, fraud_matrix, load_claim_frame
from core.config import PATHS, settings
from data.reference import APPROVED

PARAMS = dict(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.9,
              colsample_bytree=0.9, min_child_weight=2, reg_lambda=1.0, tree_method="hist")


def _hash_frame(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()[:8]


def _version(name: str, X: pd.DataFrame) -> str:
    return f"{name}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{_hash_frame(X)}"


def _binary(X: pd.DataFrame, y: pd.Series, name: str, seed: int) -> tuple[xgb.XGBClassifier, dict]:
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, stratify=y, random_state=seed)
    pos_w = float((ytr == 0).sum() / max((ytr == 1).sum(), 1))
    model = xgb.XGBClassifier(**PARAMS, scale_pos_weight=pos_w, eval_metric="aucpr", random_state=seed)
    model.fit(Xtr, ytr)
    p = model.predict_proba(Xte)[:, 1]
    metrics = {"roc_auc": round(roc_auc_score(yte, p), 4), "pr_auc": round(average_precision_score(yte, p), 4),
               "accuracy@0.5": round(accuracy_score(yte, p >= 0.5), 4), "positives_in_test": int(yte.sum()),
               "n_train": len(Xtr), "n_test": len(Xte)}
    print(f"  {name:<22} ROC-AUC={metrics['roc_auc']:.3f}  PR-AUC={metrics['pr_auc']:.3f}  "
          f"acc={metrics['accuracy@0.5']:.3f}")
    model.fit(X, y)  # refit on all data for serving; metrics come from the held-out split above
    return model, metrics


def main() -> None:
    seed = settings.seed
    PATHS.models.mkdir(parents=True, exist_ok=True)
    df = load_claim_frame()
    print(f"Training on {len(df):,} claims")
    meta: dict = {"trained_at": datetime.now(timezone.utc).isoformat(), "xgboost": xgb.__version__, "models": {}}

    # ---- 1. denial risk ------------------------------------------------------
    Xd, yd = denial_matrix(df), df["denied"].astype(int)
    denial, m = _binary(Xd, yd, "denial_model", seed)
    denial.save_model(PATHS.models / "denial_model.json")
    meta["models"]["denial_model"] = {"version": _version("denial-xgb", Xd), "features": list(Xd.columns), "metrics": m}

    # ---- 2. denial reason (multiclass on denied claims) ---------------------
    den = df[df["denied"] == 1]
    # Drop reason codes too rare to learn (e.g. a dataset where the SIU caught nobody).
    counts = den["denial_reason"].value_counts()
    den = den[den["denial_reason"].isin(counts[counts >= 5].index)]
    Xr = denial_matrix(den)
    classes = sorted(den["denial_reason"].unique())
    yr = den["denial_reason"].map({c: i for i, c in enumerate(classes)})
    Xtr, Xte, ytr, yte = train_test_split(Xr, yr, test_size=0.25, stratify=yr, random_state=seed)
    reason = xgb.XGBClassifier(**PARAMS, objective="multi:softprob", eval_metric="mlogloss", random_state=seed)
    reason.fit(Xtr, ytr)
    pred = reason.predict(Xte)
    rm = {"accuracy": round(accuracy_score(yte, pred), 4), "macro_f1": round(f1_score(yte, pred, average="macro"), 4),
          "n_train": len(Xtr), "n_test": len(Xte)}
    print(f"  {'reason_model':<22} accuracy={rm['accuracy']:.3f}  macro-F1={rm['macro_f1']:.3f}  classes={classes}")
    reason.fit(Xr, yr)
    reason.save_model(PATHS.models / "reason_model.json")
    meta["models"]["reason_model"] = {"version": _version("reason-xgb", Xr), "features": list(Xr.columns),
                                      "classes": classes, "metrics": rm}

    # ---- 3. fraud: with vs without graph features -----------------------------
    yf = df["is_planted_fraud"].astype(int)
    _, base_m = _binary(fraud_matrix(df, with_graph=False), yf, "fraud (claim-only)", seed)
    Xf = fraud_matrix(df, with_graph=True)
    fraud, fm = _binary(Xf, yf, "fraud (claim+graph)", seed)
    fraud.save_model(PATHS.models / "fraud_model.json")
    fm["baseline_claim_only"] = base_m
    meta["models"]["fraud_model"] = {"version": _version("fraud-xgb", Xf), "features": list(Xf.columns), "metrics": fm}
    print(f"  -> graph features change fraud PR-AUC by {fm['pr_auc'] - base_m['pr_auc']:+.3f}")

    # Sanity: how many wrongful denials does the denial model flag as high risk?
    risk = denial.predict_proba(Xd)[:, 1]
    wrong = df["denial_wrongful"] == 1
    meta["models"]["denial_model"]["wrongful_denials_flagged@0.5"] = f"{int((risk[wrong] >= 0.5).sum())}/{int(wrong.sum())}"

    (PATHS.models / "metadata.json").write_text(json.dumps(meta, indent=2))
    print(f"Models + metadata -> {PATHS.models}")
    for k, v in meta["models"].items():
        print(f"  {k:<14} version {v['version']}")


if __name__ == "__main__":
    np.random.seed(settings.seed)
    main()
