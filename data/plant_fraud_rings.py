"""
data/plant_fraud_rings.py — inject KNOWN fraud rings into the dataset, as FHIR.

WHY plant fraud at all?
  Neither Synthea nor our generator produces fraud. To build and *measure* a
  fraud detector you need ground truth, so we plant rings whose members we
  know and save the answer key to data/planted_fhir/ground_truth.json. The
  detectors (graph/ Cypher queries, Louvain, the XGBoost fraud model) never
  read that file; only the evaluation step does.

The two ring patterns (both well documented in health-insurance fraud):

  RING-A  "Identity-sharing phantom billing"
          3 newly opened clinics in one city share the same 4 doctors. 14
          "patients" are registered at 2 addresses, and most give the same
          phone number (the organiser's). Each patient racks up 5-8 high-value
          claims across all three clinics within ~75 days.
          Graph signature: a dense Patient–Clinic–Doctor block plus
          many Patients -> one Phone/Address.

  RING-B  "Referral kickback loop + threshold structuring"
          5 doctors at 2 clinics refer every patient around a closed loop
          D0 -> D1 -> D2 -> D3 -> D4 -> D0. Each bill is kept just below the plan's
          pre-authorisation threshold, so no insurer review is triggered.
          Graph signature: a directed CYCLE in (:Practitioner)-[:REFERRED_TO]->
          and a pile of amounts at 90-99% of a threshold.

Camouflage: every ring patient also makes one ordinary visit to a legitimate
hospital, so the ring is not a trivially disconnected island. Simple
connected-components alone will not isolate it; community detection
(Louvain) still will. That is a lesson the graph module demonstrates.

Run (after Synthea or the generator):  python -m data.plant_fraud_rings
"""
from __future__ import annotations

import json
import random
import shutil
from datetime import datetime, timedelta

from core.config import PATHS, settings
from data import fhir_builders as fb
from data import synthetic_names as names
from data.reference import PLANS


def _legit_hospitals() -> list[tuple[str, str]]:
    """(identifier-value, name) of existing organisations to use as camouflage."""
    found: list[tuple[str, str]] = []
    for d in (PATHS.synthea_fhir, PATHS.generated_fhir):
        for f in sorted(d.glob("hospitalInformation*.json")) if d.exists() else []:
            for e in json.loads(f.read_text())["entry"]:
                r = e["resource"]
                if r["resourceType"] == "Organization":
                    found.append((r["identifier"][0]["value"], r.get("name", "Hospital")))
    if not found:
        raise SystemExit("No hospitalInformation*.json found. Run Synthea or "
                         "`python -m data.generate_synthetic_fhir` first.")
    return found


def _doc(rng: random.Random) -> dict:
    given, family, gender = names.person(rng)
    return fb.practitioner(fb.new_id(rng), f"MCI-{rng.randint(100000, 999999)}", given, family, gender)


def _dt(d: dict) -> tuple[str, str]:
    n = d["name"][0]
    return d["identifier"][0]["value"], f"Dr. {n['given'][0]} {n['family']}"


def plant(seed: int) -> dict:
    rng = random.Random(seed + 1000)  # different stream from the generator
    out = PATHS.planted_fhir
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    legit = _legit_hospitals()
    # Each ring gets its own camouflage hospital (rings are independent operations).
    camo_a, camo_b = rng.sample(legit, 2) if len(legit) > 1 else (legit[0], legit[0])
    truth = {"rings": []}
    orgs_all, docs_all = [], []
    anchor = datetime(2026, 3, 1, 9, 0)

    # ------------------------------------------------------------------ RING-A
    city, state = "Indore", "Madhya Pradesh"
    a_orgs = [fb.organization(fb.new_id(rng), n, city, state, "Clinic") for n in
              ("Om Sai Day Care Clinic, Indore", "New Life Diagnostic Centre, Indore", "Care Plus Polyclinic, Indore")]
    a_docs = [_doc(rng) for _ in range(4)]
    orgs_all += a_orgs
    docs_all += a_docs
    shared_addresses = [names.street(rng), names.street(rng)]
    organiser_phone = names.phone(rng)
    ring_a = {"ring_id": "RING-A", "pattern": "identity_sharing_phantom_billing",
              "organizations": [o["id"] for o in a_orgs], "practitioners": [d["id"] for d in a_docs],
              "patients": [], "claims": []}
    # Upcoded acute conditions billed at inflated amounts.
    a_dx = [("233604007", "Pneumonia", "IMP", ("32485007", "Hospital admission"), (90000, 190000)),
            ("10509002", "Acute bronchitis", "AMB", ("399208008", "Chest X-ray"), (6000, 15000)),
            ("65966004", "Fracture of forearm", "EMER", ("33496007", "Closed reduction of fracture"), (70000, 120000))]

    for i in range(14):
        pid = fb.new_id(rng)
        given, family, gender = names.person(rng)
        phone = organiser_phone if i < 10 else names.phone(rng)
        res = [fb.patient(pid, given, family, gender,
                          (anchor - timedelta(days=rng.randint(20 * 365, 60 * 365))).date().isoformat(),
                          shared_addresses[i % 2], city, state, phone)]
        plan = PLANS[rng.choice(["SGH-GOLD", "SGH-SILVER"])]
        cov_id = fb.new_id(rng)
        res.append(fb.coverage(cov_id, pid, plan.code, plan.name, plan.insurer,
                               (anchor - timedelta(days=rng.randint(400, 900))).date().isoformat()))
        ring_a["patients"].append(pid)

        visits = [(rng.choice(a_orgs), rng.choice(a_docs), rng.choice(a_dx)) for _ in range(rng.randint(5, 8))]
        visits.append(("CAMO", rng.choice(a_docs), a_dx[1]))  # one visit to a legitimate hospital
        for org, doc, (code, disp, enc_class, proc, cost) in visits:
            start = anchor + timedelta(days=rng.randint(0, 75), hours=rng.randint(8, 18))
            org_t = camo_a if org == "CAMO" else (org["id"], org["name"])
            eid, cid, clid = fb.new_id(rng), fb.new_id(rng), fb.new_id(rng)
            los = timedelta(days=rng.randint(1, 2)) if enc_class == "IMP" else timedelta(minutes=40)
            res.append(fb.encounter(eid, pid, enc_class, "Encounter for symptom", start, start + los,
                                    org_t, _dt(doc), reason=(code, disp)))
            res.append(fb.condition(cid, pid, eid, code, disp, start))
            res.append(fb.claim(clid, pid, eid, cov_id, org_t, _dt(doc), start + los, [cid], proc, rng.uniform(*cost)))
            if org != "CAMO":
                ring_a["claims"].append(clid)
        (out / f"{pid}.json").write_text(json.dumps(fb.bundle(res)))
    truth["rings"].append(ring_a)

    # ------------------------------------------------------------------ RING-B
    city, state = "Lucknow", "Uttar Pradesh"
    b_orgs = [fb.organization(fb.new_id(rng), n, city, state, "Clinic") for n in
              ("Lifeline Ortho & Physio Clinic, Lucknow", "Sanjeevani Polyclinic, Lucknow")]
    b_docs = [_doc(rng) for _ in range(5)]
    orgs_all += b_orgs
    docs_all += b_docs
    ring_b = {"ring_id": "RING-B", "pattern": "referral_loop_threshold_structuring",
              "organizations": [o["id"] for o in b_orgs], "practitioners": [d["id"] for d in b_docs],
              "patients": [], "claims": []}
    b_dx = [("239873007", "Osteoarthritis of knee", ("91251008", "Physical therapy procedure")),
            ("44465007", "Sprain of ankle", ("91251008", "Physical therapy procedure")),
            ("161891005", "Back pain", ("229070002", "Spinal manipulation"))]

    for i in range(10):
        pid = fb.new_id(rng)
        given, family, gender = names.person(rng)
        res = [fb.patient(pid, given, family, gender,
                          (anchor - timedelta(days=rng.randint(25 * 365, 70 * 365))).date().isoformat(),
                          names.street(rng), city, state, names.phone(rng))]
        plan = PLANS[rng.choice(["SGH-SILVER", "SGH-GOLD"])]
        cov_id = fb.new_id(rng)
        res.append(fb.coverage(cov_id, pid, plan.code, plan.name, plan.insurer,
                               (anchor - timedelta(days=rng.randint(400, 900))).date().isoformat()))
        ring_b["patients"].append(pid)

        k = rng.randrange(5)  # where in the loop this patient enters
        start = anchor + timedelta(days=rng.randint(0, 60))
        n_visits = rng.randint(4, 6)
        for step in range(n_visits):
            doc, ref = b_docs[(k + step + 1) % 5], b_docs[(k + step) % 5]   # ref -> doc follows the loop
            org = b_orgs[(k + step) % 2]
            code, disp, proc = rng.choice(b_dx)
            # Threshold structuring: 90-99% of the pre-auth trigger amount.
            amount = plan.preauth_threshold_inr * rng.uniform(0.90, 0.99)
            eid, cid, clid = fb.new_id(rng), fb.new_id(rng), fb.new_id(rng)
            res.append(fb.encounter(eid, pid, "AMB", "Encounter for problem", start, start + timedelta(minutes=50),
                                    (org["id"], org["name"]), _dt(doc), reason=(code, disp), referrer=_dt(ref)))
            res.append(fb.condition(cid, pid, eid, code, disp, start))
            res.append(fb.claim(clid, pid, eid, cov_id, (org["id"], org["name"]), _dt(doc), start, [cid], proc, amount))
            ring_b["claims"].append(clid)
            start += timedelta(days=rng.randint(4, 12))
        # Camouflage visit.
        eid, cid, clid = fb.new_id(rng), fb.new_id(rng), fb.new_id(rng)
        res.append(fb.encounter(eid, pid, "AMB", "Encounter for symptom", start, start + timedelta(minutes=30),
                                camo_b, _dt(b_docs[k]), reason=("444814009", "Viral sinusitis")))
        res.append(fb.condition(cid, pid, eid, "444814009", "Viral sinusitis", start))
        res.append(fb.claim(clid, pid, eid, cov_id, camo_b, _dt(b_docs[k]), start, [cid],
                            ("11429006", "Consultation"), rng.uniform(600, 1800)))
        (out / f"{pid}.json").write_text(json.dumps(fb.bundle(res)))
    truth["rings"].append(ring_b)

    (out / "hospitalInformation_planted.json").write_text(json.dumps(fb.bundle(orgs_all)))
    (out / "practitionerInformation_planted.json").write_text(json.dumps(fb.bundle(docs_all)))
    (out / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    return truth


if __name__ == "__main__":
    t = plant(settings.seed)
    for r in t["rings"]:
        print(f"{r['ring_id']}: {len(r['patients'])} patients, {len(r['practitioners'])} doctors, "
              f"{len(r['organizations'])} clinics, {len(r['claims'])} fraudulent claims")
    print(f"Answer key -> {PATHS.planted_fhir / 'ground_truth.json'}")
