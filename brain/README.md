# brain/: the ML models (denial risk, reason codes, fraud)

## What it does
| File | Role |
|---|---|
| `features.py` | Builds the **denial** feature matrix (rule inputs: amounts vs thresholds, pre-auth, documents, timing vs policy start, plan, category) and the **fraud** matrix (graph features + a few claim fields). `FEATURE_TEXT` gives plain-English names for the UI. |
| `train.py` | Trains three XGBoost models, prints held-out metrics, and saves models in XGBoost's JSON format plus `models/metadata.json` (versions, features, metrics). |
| `predict.py` | `get_brain()` loads the models once and serves `denial(claim_id)`, `fraud(claim_id)` and `scores()`, with **TreeSHAP** drivers. |

| Model | Target | Trained on |
|---|---|---|
| `denial_model` | P(denied) | all claims |
| `reason_model` | P(reason \| denied), 7 classes D01–D07 | denied claims only |
| `fraud_model` | P(claim in a planted ring) | all claims, with graph features |

The top reason codes shown are P(denied) × P(reason | denied).

## Why these choices
- **XGBoost** suits tabular data with interactions such as "chronic AND inside waiting window AND Silver plan", and it computes exact SHAP values natively (`pred_contribs=True`).
- **Raw rule inputs, not rule outcomes.** That lets the model learn the insurer's real behaviour, including its PED bug. On a wrongful-denial claim, look at the SHAP drivers: `chronic` pushes risk up even though `onset_before_cover = 0`.
- **No label leakage in fraud.** The insurer's denial outcome is excluded from fraud features, because it partly *results* from fraud (D07).
- **Baseline comparison.** `train.py` also fits a claim-only fraud model and prints how much the graph features add.
- **Versioning.** Each model gets `name-timestamp-datahash`. That string is written into every audit record.

## How it fits the whole
Inputs: `data/processed/claims_labeled.csv` + `graph_features.csv` + `patients.csv`. Used by the orchestrator, the appeal agent (denial context) and the API's denial and claims endpoints.

## How to run
```bash
python -m brain.train
python -m brain.predict                 # explain the highest-risk claim
python -m brain.predict <claim_id>
```

## How to verify
- Training prints ROC-AUC and PR-AUC. Expect denial ROC-AUC around 0.95 and fraud PR-AUC around 0.90 claim-only versus around 1.0 with graph features (synthetic rings are clean).
- `brain/models/metadata.json` exists and includes `wrongful_denials_flagged@0.5`: the model anticipates most wrongful denials, which is what makes proactive appeals possible.
- In the UI's **Denial risk** view, SHAP bars should point at sensible features (pre-auth missing → D01, and so on).
