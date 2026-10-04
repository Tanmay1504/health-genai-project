"""
data/fhir_builders.py — small helpers that emit FHIR R4 resources shaped like
Synthea's output.

Used by:
  * data/generate_synthetic_fhir.py  (fallback when you can't run Synthea)
  * data/plant_fraud_rings.py        (injects fraud rings as *FHIR*, not CSV)

WHY imitate Synthea's quirks?
  Synthea references in-bundle resources with `urn:uuid:<id>` and shared
  resources (hospitals, doctors) with *conditional references* such as
      "Organization?identifier=https://github.com/synthetichealth/synthea|<id>"
  If our generated data used a different style, the parser would have two code
  paths and only one would get tested. By copying the style, one parser
  (data/parse_fhir.py) handles Synthea, generated and planted data the same way.

FHIR primer for this project
  Patient       — the insured person
  Organization  — a clinic / hospital (the "provider" on a claim)
  Practitioner  — a doctor
  Encounter     — one visit; links Patient, Organization (serviceProvider)
                  and Practitioners (participant: PPRF = primary performer,
                  REF = referrer)
  Condition     — a diagnosis (SNOMED CT code), usually tied to an Encounter
  Coverage      — the insurance policy; `class[plan].value` holds our plan code
  Claim         — the bill: patient, provider, diagnosis refs, items, total
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime

SNOMED = "http://snomed.info/sct"
ORG_SYSTEM = "https://github.com/synthetichealth/synthea"
# Fake Indian medical-registration identifier system (illustrative only).
PRACT_SYSTEM = "https://example.org/fhir/sid/in-medical-registration"


def new_id(rng: random.Random) -> str:
    """Reproducible UUID: drawn from the seeded RNG, not from os.urandom."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")  # IST offset


def org_ref(org_id: str, name: str) -> dict:
    return {"reference": f"Organization?identifier={ORG_SYSTEM}|{org_id}", "display": name}


def pract_ref(reg_no: str, name: str) -> dict:
    return {"reference": f"Practitioner?identifier={PRACT_SYSTEM}|{reg_no}", "display": name}


def coding(code: str, display: str, system: str = SNOMED) -> dict:
    return {"coding": [{"system": system, "code": code, "display": display}], "text": display}


# --- shared resources ----------------------------------------------------------
def organization(org_id: str, name: str, city: str, state: str, kind: str) -> dict:
    return {
        "resourceType": "Organization",
        "id": org_id,
        "identifier": [{"system": ORG_SYSTEM, "value": org_id}],
        "active": True,
        "type": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/organization-type",
                              "code": "prov", "display": "Healthcare Provider"}], "text": kind}],
        "name": name,
        "address": [{"city": city, "state": state, "country": "IN"}],
    }


def practitioner(pid: str, reg_no: str, given: str, family: str, gender: str) -> dict:
    return {
        "resourceType": "Practitioner",
        "id": pid,
        "identifier": [{"system": PRACT_SYSTEM, "value": reg_no}],
        "active": True,
        "name": [{"family": family, "given": [given], "prefix": ["Dr."]}],
        "gender": gender,
    }


# --- per-patient resources -----------------------------------------------------
def patient(pid: str, given: str, family: str, gender: str, birth: str,
            line: str, city: str, state: str, phone: str) -> dict:
    return {
        "resourceType": "Patient",
        "id": pid,
        "name": [{"use": "official", "family": family, "given": [given]}],
        "telecom": [{"system": "phone", "value": phone, "use": "home"}],
        "gender": gender,
        "birthDate": birth,
        "address": [{"line": [line], "city": city, "state": state, "country": "IN"}],
    }


def coverage(cid: str, patient_id: str, plan_code: str, plan_name: str, insurer: str, start: str) -> dict:
    return {
        "resourceType": "Coverage",
        "id": cid,
        "status": "active",
        "subscriberId": f"POL-{cid[:8].upper()}",
        "beneficiary": {"reference": f"urn:uuid:{patient_id}"},
        "period": {"start": start},
        "payor": [{"display": insurer}],
        "class": [{"type": {"coding": [{"code": "plan"}]}, "value": plan_code, "name": plan_name}],
    }


def encounter(eid: str, patient_id: str, enc_class: str, type_display: str, start: datetime,
              end: datetime, org: tuple[str, str], performer: tuple[str, str],
              reason: tuple[str, str] | None = None, referrer: tuple[str, str] | None = None) -> dict:
    participants = [{
        "type": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ParticipationType",
                              "code": "PPRF", "display": "primary performer"}]}],
        "individual": pract_ref(*performer),
    }]
    if referrer:
        # REF = "referrer" in the HL7 v3 ParticipationType code system. The graph
        # loader turns this into (:Practitioner)-[:REFERRED_TO]->(:Practitioner),
        # which is how referral-kickback loops become visible.
        participants.append({
            "type": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ParticipationType",
                                  "code": "REF", "display": "referrer"}]}],
            "individual": pract_ref(*referrer),
        })
    res = {
        "resourceType": "Encounter",
        "id": eid,
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": enc_class},
        "type": [coding("185345009", type_display)],
        "subject": {"reference": f"urn:uuid:{patient_id}"},
        "participant": participants,
        "period": {"start": iso(start), "end": iso(end)},
        "serviceProvider": org_ref(*org),
    }
    if reason:
        res["reasonCode"] = [coding(*reason)]
    return res


def condition(cid: str, patient_id: str, encounter_id: str, code: str, display: str, onset: datetime) -> dict:
    return {
        "resourceType": "Condition",
        "id": cid,
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "verificationStatus": {"coding": [{"code": "confirmed"}]},
        "category": [{"coding": [{"code": "encounter-diagnosis"}]}],
        "code": coding(code, f"{display} (disorder)"),
        "subject": {"reference": f"urn:uuid:{patient_id}"},
        "encounter": {"reference": f"urn:uuid:{encounter_id}"},
        "onsetDateTime": iso(onset),
        "recordedDate": iso(onset),
    }


def claim(cl_id: str, patient_id: str, encounter_id: str, coverage_id: str, org: tuple[str, str],
          performer: tuple[str, str], created: datetime, condition_ids: list[str],
          proc: tuple[str, str], amount_inr: float) -> dict:
    return {
        "resourceType": "Claim",
        "id": cl_id,
        "status": "active",
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "institutional"}]},
        "use": "claim",
        "patient": {"reference": f"urn:uuid:{patient_id}"},
        "billablePeriod": {"start": iso(created), "end": iso(created)},
        "created": iso(created),
        "provider": org_ref(*org),
        "priority": {"coding": [{"code": "normal"}]},
        "careTeam": [{"sequence": 1, "provider": pract_ref(*performer)}],
        "diagnosis": [{"sequence": i + 1, "diagnosisReference": {"reference": f"urn:uuid:{c}"}}
                      for i, c in enumerate(condition_ids)],
        "insurance": [{"sequence": 1, "focal": True, "coverage": {"reference": f"urn:uuid:{coverage_id}"}}],
        "item": [{
            "sequence": 1,
            "productOrService": coding(*proc),
            "encounter": [{"reference": f"urn:uuid:{encounter_id}"}],
            "net": {"value": round(amount_inr, 2), "currency": "INR"},
        }],
        "total": {"value": round(amount_inr, 2), "currency": "INR"},
    }


def bundle(resources: list[dict]) -> dict:
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r,
                   "request": {"method": "POST", "url": r["resourceType"]}} for r in resources],
    }
