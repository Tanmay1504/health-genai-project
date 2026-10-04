"""
data/generate_synthetic_fhir.py — FALLBACK generator of Synthea-shaped FHIR.

Use this when you can't (or don't want to) run Synthea, e.g. without Java 17.
It writes data/generated_fhir/:
    hospitalInformation.json      (Organizations — same file name Synthea uses)
    practitionerInformation.json  (Practitioners)
    <patient-id>.json             (one transaction Bundle per patient)

Realism knobs that matter for the downstream models:
  * HOUSEHOLDS: ~15% of patients share an address (and often a phone) with
    family. Fraud rings also share addresses, so "shared address" alone must
    not be a perfect fraud signal; the graph model has to combine signals.
  * MOONLIGHTING: ~20% of doctors legitimately work at 2 clinics, so "doctor at
    several clinics" is also only a weak signal by itself.
  * PRE-EXISTING DISEASE: chronic conditions often start years before the
    policy, so waiting-period denials (D03) occur naturally.

Run:  python -m data.generate_synthetic_fhir --patients 400
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from datetime import datetime, timedelta

from core.config import PATHS, settings
from data import fhir_builders as fb
from data import synthetic_names as names
from data.reference import CONDITION_CATALOG, PLANS, category_for, is_chronic

AS_OF = datetime(2026, 9, 30, 10, 0)  # fixed "today" so runs are reproducible

CHRONIC = [c for c in CONDITION_CATALOG if is_chronic(c["display"])]
ACUTE = [c for c in CONDITION_CATALOG if not is_chronic(c["display"])]


def build_providers(rng: random.Random, n_orgs: int = 15, n_docs: int = 45):
    """Clinics/hospitals and doctors. Returns (org_resources, doc_resources, staff_by_org)."""
    kinds = ["Multispeciality Hospital"] * 5 + ["Clinic"] * 6 + ["Eye Centre"] * 2 + ["Diagnostics"] * 2
    orgs = []
    for i in range(n_orgs):
        city, state = names.CITIES[i % len(names.CITIES)]
        kind = kinds[i % len(kinds)]
        oid = fb.new_id(rng)
        orgs.append(fb.organization(oid, f"{names.FACILITY_PREFIX[i]} {kind}, {city}", city, state, kind))

    docs, staff = [], {o["id"]: [] for o in orgs}
    for _ in range(n_docs):
        given, family, gender = names.person(rng)
        reg = f"MCI-{rng.randint(100000, 999999)}"
        d = fb.practitioner(fb.new_id(rng), reg, given, family, gender)
        docs.append(d)
        home = rng.choice(orgs)
        staff[home["id"]].append(d)
        if rng.random() < 0.20:  # legitimate second affiliation
            other = rng.choice([o for o in orgs if o["id"] != home["id"]])
            staff[other["id"]].append(d)
    # Guarantee every org has at least one doctor.
    for o in orgs:
        if not staff[o["id"]]:
            staff[o["id"]].append(rng.choice(docs))
    return orgs, docs, staff


def doc_tuple(d: dict) -> tuple[str, str]:
    n = d["name"][0]
    return d["identifier"][0]["value"], f"Dr. {n['given'][0]} {n['family']}"


def org_tuple(o: dict) -> tuple[str, str]:
    return o["id"], o["name"]


def generate(n_patients: int, seed: int, claim_years: int) -> int:
    rng = random.Random(seed)
    out = PATHS.generated_fhir
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    orgs, docs, staff = build_providers(rng)
    (out / "hospitalInformation.json").write_text(json.dumps(fb.bundle(orgs)))
    (out / "practitionerInformation.json").write_text(json.dumps(fb.bundle(docs)))
    hospitals = [o for o in orgs if "Hospital" in o["name"]]
    eye_centres = [o for o in orgs if "Eye" in o["name"]]

    window_start = AS_OF - timedelta(days=365 * claim_years)
    plan_codes, plan_weights = list(PLANS), [0.50, 0.35, 0.15]
    household: tuple[str, str, str, str] | None = None  # (line, city, state, phone) to reuse

    for _ in range(n_patients):
        pid = fb.new_id(rng)
        given, family, gender = names.person(rng)
        birth = AS_OF - timedelta(days=rng.randint(18 * 365, 80 * 365))

        # Household logic: 15% chance to move in with the previous patient's family.
        if household and rng.random() < 0.15:
            line, city, state, ph = household
            if rng.random() < 0.4:
                ph = names.phone(rng)  # family members often have their own phone
        else:
            city, state = rng.choice(names.CITIES)
            line, ph = names.street(rng), names.phone(rng)
        household = (line, city, state, ph)

        resources: list[dict] = [fb.patient(pid, given, family, gender, birth.date().isoformat(), line, city, state, ph)]

        plan = PLANS[rng.choices(plan_codes, weights=plan_weights)[0]]
        cov_start = window_start + timedelta(days=rng.randint(-180, 900))
        cov_id = fb.new_id(rng)
        resources.append(fb.coverage(cov_id, pid, plan.code, plan.name, plan.insurer, cov_start.date().isoformat()))

        # Chronic diseases, some diagnosed long before cover started (=> PED).
        chronic_dx: list[tuple[dict, datetime, str]] = []  # (catalog item, onset, condition id)
        local_orgs = [o for o in orgs if o["address"][0]["city"] == city] or orgs
        home_org = rng.choice(local_orgs)

        for item in rng.sample(CHRONIC, k=rng.choice([0, 0, 1, 1, 2])):
            onset = cov_start - timedelta(days=rng.randint(-1100, 1800))
            onset = min(onset, AS_OF - timedelta(days=30))
            # The diagnosing encounter itself (may be before the claim window; still recorded).
            eid, cid = fb.new_id(rng), fb.new_id(rng)
            doc = rng.choice(staff[home_org["id"]])
            resources.append(fb.encounter(eid, pid, "AMB", "Encounter for problem", onset, onset + timedelta(minutes=30),
                                          org_tuple(home_org), doc_tuple(doc)))
            resources.append(fb.condition(cid, pid, eid, item["code"], item["display"], onset))
            chronic_dx.append((item, onset, cid))

        # Claimable encounters inside the claim window.
        for _ in range(rng.randint(2, 9)):
            start = window_start + timedelta(days=rng.randint(0, 365 * claim_years - 1), hours=rng.randint(8, 18))
            eligible_chronic = [c for c in chronic_dx if c[1] <= start]
            eid = fb.new_id(rng)

            if eligible_chronic and rng.random() < 0.5:
                # Follow-up for a chronic disease: like Synthea, no new Condition,
                # the link is via Encounter.reasonCode + Claim.diagnosis -> old Condition.
                item, _onset, cond_id = rng.choice(eligible_chronic)
                if item["enc"] == "IMP" and rng.random() < 0.25:
                    enc_class, proc, cost = "IMP", item["proc"], item["cost"]
                else:
                    enc_class, proc, cost = "AMB", ("11429006", "Consultation"), (600, 3500)
                cond_ids, type_disp = [cond_id], "Follow-up encounter"
                reason = (item["code"], item["display"])
            else:
                pool = ACUTE if gender == "female" else [c for c in ACUTE if "pregnancy" not in c["display"]]
                item = rng.choices(pool, weights=[c["weight"] for c in pool])[0]
                enc_class, proc, cost = item["enc"], item["proc"], item["cost"]
                cid = fb.new_id(rng)
                cond_ids, type_disp, reason = [cid], "Encounter for symptom", (item["code"], item["display"])

            # Members of a hospitalisation-only scheme rarely file OPD bills;
            # keep ~30% of them so the "not covered" (D02) pattern still exists.
            if (not plan.opd_covered and enc_class == "AMB"
                    and category_for(item["display"]) not in plan.daycare_categories and rng.random() < 0.7):
                continue

            # Where: inpatient -> a hospital, cataract -> eye centre, else home clinic (mostly).
            if enc_class in ("IMP", "EMER"):
                org = rng.choice(hospitals)
            elif "Cataract" in item["display"] and eye_centres:
                org = rng.choice(eye_centres)
            else:
                org = home_org if rng.random() < 0.75 else rng.choice(local_orgs)
            doc = rng.choice(staff[org["id"]])
            los = timedelta(days=rng.randint(2, 7)) if enc_class == "IMP" else timedelta(minutes=45)

            resources.append(fb.encounter(eid, pid, enc_class, type_disp, start, start + los,
                                          org_tuple(org), doc_tuple(doc), reason=reason))
            if type_disp != "Follow-up encounter":
                resources.append(fb.condition(cond_ids[0], pid, eid, item["code"], item["display"], start))
            amount = rng.uniform(*cost)
            resources.append(fb.claim(fb.new_id(rng), pid, eid, cov_id, org_tuple(org), doc_tuple(doc),
                                      start + los, cond_ids, proc, amount))

        (out / f"{pid}.json").write_text(json.dumps(fb.bundle(resources)))
    return n_patients


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patients", type=int, default=400)
    args = ap.parse_args()
    n = generate(args.patients, settings.seed, settings.claim_years)
    print(f"Wrote {n} patient bundles + providers to {PATHS.generated_fhir}")
