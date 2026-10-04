"""
preauth/fhir_reader.py — read ONE patient's FHIR bundle and pull out what a
pre-authorisation form needs: patient, coverage, encounter, diagnoses.

WHY read the raw FHIR bundle instead of the processed CSVs?
  The pre-auth step represents "a hospital system sends us the patient's FHIR
  record". Reading the bundle directly shows the real FHIR navigation (resource
  types, references, codings) that an integration would do. The CSVs are only
  used for things FHIR doesn't carry in Synthea's output (a synthesized
  Coverage, a resolved doctor name).
"""
from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache

import pandas as pd

from core.config import PATHS


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    df = pd.read_csv(PATHS.processed / "patient_bundles.csv")
    return dict(zip(df.patient_id, df.path))


def load_bundle(patient_id: str) -> dict:
    path = _index().get(patient_id)
    if not path:
        raise KeyError(f"No FHIR bundle for patient {patient_id}")
    return json.loads((PATHS.root / path).read_text())


def _strip_ref(ref: str | None) -> str | None:
    return ref.removeprefix("urn:uuid:") if ref else None


def _display(cc: dict) -> tuple[str | None, str]:
    c = (cc.get("coding") or [{}])[0]
    disp = re.sub(r"\s*\([^()]*\)\s*$", "", c.get("display") or cc.get("text", "")).strip()
    return c.get("code"), disp


def extract(patient_id: str, encounter_id: str | None = None) -> dict:
    """Return a plain dict: {patient, coverage_fhir, encounter, diagnoses, claim_hint}.

    Encounter choice: the requested one, else the most recent INPATIENT stay
    (pre-auth is mostly about admissions), else the most recent encounter.
    """
    b = load_bundle(patient_id)
    by_type: dict[str, list[dict]] = {}
    for e in b["entry"]:
        by_type.setdefault(e["resource"]["resourceType"], []).append(e["resource"])

    p = by_type["Patient"][0]
    name = (p.get("name") or [{}])[0]
    addr = (p.get("address") or [{}])[0]
    patient = {
        "patient_id": p["id"],
        "name": re.sub(r"\d+", "", " ".join(name.get("given", [])) + " " + name.get("family", "")).strip(),
        "gender": p.get("gender", ""),
        "birth_date": p.get("birthDate", ""),
        "phone": next((t["value"] for t in p.get("telecom", []) if t.get("system") == "phone"), ""),
        "address": ", ".join(filter(None, [" ".join(addr.get("line", [])), addr.get("city"), addr.get("state")])),
    }

    encounters = sorted(by_type.get("Encounter", []), key=lambda e: e.get("period", {}).get("start", ""))
    if encounter_id:
        enc = next((e for e in encounters if e["id"] == encounter_id), None)
        if enc is None:
            raise KeyError(f"Encounter {encounter_id} not in patient bundle")
    else:
        inpatient = [e for e in encounters if e.get("class", {}).get("code") == "IMP"]
        enc = (inpatient or encounters or [None])[-1]
    if enc is None:
        raise KeyError("Patient has no encounters")

    performer = next((pt for pt in enc.get("participant", [])
                      if any(c.get("code") == "PPRF" for t in pt.get("type", []) for c in t.get("coding", []))),
                     (enc.get("participant") or [{}])[0])
    encounter = {
        "encounter_id": enc["id"],
        "encounter_class": enc.get("class", {}).get("code", "AMB"),
        "type": _display((enc.get("type") or [{}])[0])[1],
        "start": enc.get("period", {}).get("start", "")[:10],
        "end": enc.get("period", {}).get("end", "")[:10],
        "provider_org": enc.get("serviceProvider", {}).get("display", ""),
        "treating_doctor": performer.get("individual", {}).get("display", ""),
    }

    # Diagnoses: Conditions recorded at this encounter + the encounter's reasonCode.
    conds = by_type.get("Condition", [])
    first_onset: dict[str, str] = {}
    for c in conds:
        code, _ = _display(c.get("code", {}))
        onset = (c.get("onsetDateTime") or "")[:10]
        if code and onset and (code not in first_onset or onset < first_onset[code]):
            first_onset[code] = onset
    diagnoses = []
    for c in conds:
        if _strip_ref(c.get("encounter", {}).get("reference")) == enc["id"]:
            code, disp = _display(c.get("code", {}))
            diagnoses.append({"code": code, "display": disp, "onset": first_onset.get(code), "is_primary": not diagnoses})
    for rc in enc.get("reasonCode", []):
        code, disp = _display(rc)
        if code and all(d["code"] != code for d in diagnoses):
            diagnoses.append({"code": code, "display": disp, "onset": first_onset.get(code), "is_primary": not diagnoses})

    # The claim billed for this encounter (if any) gives the requested service + cost.
    claim_hint = None
    for cl in by_type.get("Claim", []):
        encs = [_strip_ref(en.get("reference")) for it in cl.get("item", []) for en in it.get("encounter", [])]
        if enc["id"] in encs:
            item = (cl.get("item") or [{}])[-1]
            total = cl.get("total", {})
            amount = float(total.get("value", 0))
            if total.get("currency", "USD") == "USD":
                from data.parse_fhir import USD_TO_INR
                amount *= USD_TO_INR
            claim_hint = {"claim_id": cl["id"], "service": _display(item.get("productOrService", {}))[1],
                          "service_code": _display(item.get("productOrService", {}))[0], "amount_inr": round(amount, 2)}
            break

    coverage_fhir = None
    for cv in by_type.get("Coverage", []):
        plan = next((c.get("value") for c in cv.get("class", [])), None)
        coverage_fhir = {"coverage_id": cv["id"], "plan_code": plan, "start": cv.get("period", {}).get("start"),
                         "policy_number": cv.get("subscriberId")}
    return {"patient": patient, "coverage_fhir": coverage_fhir, "encounter": encounter,
            "diagnoses": diagnoses, "claim_hint": claim_hint}


def coverage_from_tables(patient_id: str) -> dict | None:
    """Fallback for Synthea data (no Coverage resource): the synthesized row."""
    cov = pd.read_csv(PATHS.processed / "coverages.csv")
    row = cov[cov.patient_id == patient_id]
    if row.empty:
        return None
    r = row.iloc[0]
    return {"coverage_id": r.coverage_id, "plan_code": r.plan_code, "start": str(r.start)[:10],
            "policy_number": f"POL-{str(r.coverage_id)[:8].upper()}", "synthesized": bool(r.synthesized)}


def age_on(birth: str, on: str) -> int | None:
    try:
        b, d = date.fromisoformat(birth[:10]), date.fromisoformat(on[:10])
        return d.year - b.year - ((d.month, d.day) < (b.month, b.day))
    except ValueError:
        return None
