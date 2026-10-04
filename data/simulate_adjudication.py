"""
data/simulate_adjudication.py — simulate how the insurer decided each claim.

Real denial data is confidential, so we SIMULATE an insurer whose behaviour has
three ingredients. This mixture is what makes the downstream ML problem
interesting rather than trivial:

  1. RULES  — the insurer applies its rulebook (data/reference.evaluate_policy):
               missing pre-auth, exclusions, waiting periods, sub-limits, missing
               documents, late filing. A violation is usually denied.
  2. LENIENCY — ~8% of rule-violating claims are approved anyway (human
               discretion). The model therefore outputs a probability, not
               certainty.
  3. MISTAKES — the insurer's auto-adjudication engine has a known bug. For a
               CHRONIC condition first diagnosed AFTER the policy began, it
               still applies the pre-existing-disease waiting period (D03) if
               the claim falls inside that window. Per the policy wording that
               is wrong: a PED is, by definition, diagnosed *before* cover
               starts. ~60% of such claims are wrongly denied. A further ~3% of
               clean claims are denied at random with D04/D02 (clerical error).
               These `denial_wrongful=1` claims are exactly what the APPEAL
               agent exists to overturn, and the policy text it retrieves
               contains the clause that proves the insurer wrong.

Claim-level administrative attributes that FHIR doesn't carry (was pre-auth
obtained? were documents complete? how many days until submission?) are also
synthesized here, from a seeded RNG.

Run:  python -m data.simulate_adjudication
Out:  data/processed/claims_labeled.csv
"""
from __future__ import annotations

import random
from datetime import date

import pandas as pd

from core.config import PATHS, settings
from data.reference import APPROVED, PLANS, category_for, evaluate_policy, is_chronic, months_between, requires_preauth


def _d(v) -> date | None:
    return None if pd.isna(v) or v in ("", None) else date.fromisoformat(str(v)[:10])


def build_claim_context(row: pd.Series, coverage_start: date) -> dict:
    """The dict shape `evaluate_policy()` expects. Also reused by the API."""
    return {
        "plan_code": row["plan_code"],
        "category": row["category"],
        "encounter_class": row["encounter_class"],
        "total_inr": float(row["total_inr"]),
        "preauth_obtained": bool(row["preauth_obtained"]),
        "chronic": bool(row["chronic"]),
        "dx_onset": _d(row["dx_onset"]),
        "coverage_start": coverage_start,
        "claim_date": _d(row["created"]),
        "docs_complete": bool(row["docs_complete"]),
        "days_to_submit": int(row["days_to_submit"]),
    }


def simulate(seed: int) -> pd.DataFrame:
    rng = random.Random(seed + 7)
    p = PATHS.processed
    claims = pd.read_csv(p / "claims.csv")
    cov = pd.read_csv(p / "coverages.csv").set_index("patient_id")

    claims["plan_code"] = claims["patient_id"].map(cov["plan_code"])
    claims["coverage_start"] = claims["patient_id"].map(cov["start"])
    claims["category"] = claims["dx_display"].map(category_for)
    claims["chronic"] = claims["dx_display"].map(is_chronic).astype(int)

    rows = []
    for _, r in claims.iterrows():
        plan = PLANS[r["plan_code"]]
        fraud = r["is_planted_fraud"] == 1
        needs_pa = requires_preauth(plan, r["encounter_class"], r["total_inr"])
        # Organised rings know the paperwork rules and comply with them.
        pa_obtained = needs_pa and rng.random() < (0.95 if fraud else 0.85)
        docs_ok = rng.random() < (0.96 if fraud else 0.94)
        days = rng.randint(31, 60) if rng.random() < 0.04 else rng.randint(1, 20)
        r = r.copy()
        r["requires_preauth"], r["preauth_obtained"] = int(needs_pa), int(pa_obtained)
        r["docs_complete"], r["n_documents"] = int(docs_ok), (rng.randint(2, 6) if docs_ok else rng.randint(0, 2))
        r["days_to_submit"] = days

        cov_start = _d(r["coverage_start"])
        cdate = _d(r["created"])
        r["months_since_cover"] = months_between(cov_start, cdate)
        violations = evaluate_policy(build_claim_context(r, cov_start))
        codes = [v["code"] for v in violations]

        denied, reason, wrongful = False, APPROVED, False
        if fraud and rng.random() < 0.30:
            denied, reason = True, "D07"                       # insurer's SIU caught it
        elif codes and rng.random() > 0.08:
            denied, reason = True, codes[0]                    # rule-based denial (most severe first)
        elif not codes:
            onset = _d(r["dx_onset"])
            ped_bug = (r["chronic"] == 1 and onset is not None and onset >= cov_start
                       and r["months_since_cover"] < plan.ped_waiting_months)
            if ped_bug and rng.random() < 0.60:
                denied, reason, wrongful = True, "D03", True   # engine bug: not actually a PED
            elif rng.random() < 0.03:
                denied, reason, wrongful = True, rng.choice(["D04", "D02"]), True  # clerical error
        r["rule_violations"] = "|".join(codes)
        r["denied"], r["denial_reason"], r["denial_wrongful"] = int(denied), reason, int(wrongful)
        rows.append(r)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = simulate(settings.seed)
    df.to_csv(PATHS.processed / "claims_labeled.csv", index=False)
    print(f"Labeled {len(df):,} claims -> {PATHS.processed / 'claims_labeled.csv'}")
    print(f"  denial rate      : {df['denied'].mean():.1%}")
    print(f"  wrongful denials : {df['denial_wrongful'].sum():,}")
    print("  reasons          :", df["denial_reason"].value_counts().to_dict())
