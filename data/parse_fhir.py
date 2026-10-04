"""
data/parse_fhir.py — flatten FHIR bundles into tidy CSV tables.

Input : every FHIR dir that exists (Synthea output, generated, planted)
Output: data/processed/
          patients.csv  organizations.csv  practitioners.csv  encounters.csv
          conditions.csv  coverages.csv  claims.csv  patient_bundles.csv

WHY flatten to tables before loading Neo4j / training models?
  FHIR is a document format: deeply nested, with references in three different
  syntaxes. Graph loading, feature engineering and the API all want simple
  rows. Doing the FHIR wrangling ONCE here means every later stage is plain
  pandas / Cypher, and the CSVs are easy to inspect while you learn.

The three reference syntaxes we resolve (see `RefIndex`):
   "urn:uuid:<id>"                                 in-bundle (Synthea, ours)
   "Organization/<id>"                             literal REST-style
   "Practitioner?identifier=<system>|<value>"      conditional (Synthea's
                                                   way to point at shared
                                                   hospitals/doctors)

Indian-context adaptation (documented, deliberate):
  * Synthea bills in USD; we convert with a PPP-style factor (USD_TO_INR) so
    amounts land in plausible Indian private-care ranges.
  * Synthea's US payers (Medicare, etc.) mean nothing here. When a bundle has
    no Coverage resource, we assign one of our fictional Indian plans
    deterministically from a hash of the patient id.

Run:  python -m data.parse_fhir
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from core.config import PATHS, settings
from data.reference import PLAN_CODES, PLANS, category_for

# ₹83/$ FX rate × Indian private-care prices being roughly 1/20 of US prices ≈ 4.
# Calibrated so a typical Synthea outpatient visit lands near ₹10,000, not ₹80,000.
USD_TO_INR = 4.0  # purchasing-power style factor, not the FX rate (see docstring)
_SEMANTIC_TAG = re.compile(r"\s*\(([^()]*)\)\s*$")
_KEEP_TAGS = {"disorder", "morphologic abnormality", "", None}


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------
def _stable_int(s: str) -> int:
    """Hash that is identical across runs/machines (Python's hash() is salted)."""
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16)


def _plan_for(patient_id: str) -> str:
    """Deterministic plan mix matching the generator: 50% Silver, 35% Gold, 15% JAC."""
    bucket = _stable_int(patient_id) % 100
    return "SGH-SILVER" if bucket < 50 else "SGH-GOLD" if bucket < 85 else "JAC-BASIC"


def _date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def _clean_name(n: dict) -> str:
    # Synthea appends digits to names ("Jane123 Doe456") to make them unique.
    given = " ".join(n.get("given", []))
    return re.sub(r"\d+", "", f"{given} {n.get('family', '')}").strip()


def _split_display(display: str) -> tuple[str, str | None]:
    """'Diabetes mellitus type 2 (disorder)' -> ('Diabetes mellitus type 2', 'disorder')."""
    m = _SEMANTIC_TAG.search(display or "")
    if m:
        return display[: m.start()].strip(), m.group(1)
    return (display or "").strip(), None


class RefIndex:
    """Resolves any FHIR reference string to (resourceType, id)."""

    def __init__(self) -> None:
        self.by_identifier: dict[tuple[str, str], str] = {}  # (type, identifier-value) -> id

    def add(self, res: dict) -> None:
        for ident in res.get("identifier", []):
            if "value" in ident:
                self.by_identifier[(res["resourceType"], ident["value"])] = res["id"]

    def resolve(self, ref: str | None) -> tuple[str | None, str | None]:
        if not ref:
            return None, None
        if ref.startswith("urn:uuid:"):
            return None, ref[len("urn:uuid:"):]
        if "?identifier=" in ref:
            rtype, ident = ref.split("?identifier=", 1)
            value = ident.split("|", 1)[-1]
            # Unknown identifier -> fall back to the value itself so the row is
            # still linkable (a stub node is created for it later).
            return rtype, self.by_identifier.get((rtype, value), value)
        if "/" in ref:
            rtype, rid = ref.split("/", 1)
            return rtype, rid
        return None, ref


# -----------------------------------------------------------------------------
# main parse
# -----------------------------------------------------------------------------
def _iter_resources(path: Path):
    data = json.loads(path.read_text())
    for e in data.get("entry", []):
        yield e["resource"]


def parse() -> dict[str, pd.DataFrame]:
    dirs = PATHS.fhir_dirs()
    if not dirs:
        raise SystemExit("No FHIR found. Run Synthea (data/fetch_synthea.sh) or the generator first.")

    refs = RefIndex()
    orgs: dict[str, dict] = {}
    practs: dict[str, dict] = {}

    # ---- pass 1: shared providers (hospitalInformation*, practitionerInformation*)
    provider_files = [f for d in dirs for f in sorted(d.glob("*.json"))
                      if f.name.startswith(("hospitalInformation", "practitionerInformation"))]
    for f in provider_files:
        for r in _iter_resources(f):
            refs.add(r)
            if r["resourceType"] == "Organization":
                addr = (r.get("address") or [{}])[0]
                orgs[r["id"]] = {"org_id": r["id"], "name": r.get("name", ""), "city": addr.get("city", ""),
                                 "kind": (r.get("type") or [{}])[0].get("text", "Healthcare Provider")}
            elif r["resourceType"] == "Practitioner":
                n = (r.get("name") or [{}])[0]
                practs[r["id"]] = {"practitioner_id": r["id"],
                                   "reg_no": (r.get("identifier") or [{}])[0].get("value", ""),
                                   "name": "Dr. " + _clean_name(n), "gender": r.get("gender", "")}

    patients, encounters, conditions, coverages, claims, bundles = [], [], [], [], [], []
    source_of = {PATHS.synthea_fhir: "synthea", PATHS.generated_fhir: "generated", PATHS.planted_fhir: "planted"}

    # ---- pass 2: one bundle per patient
    patient_files = [(d, f) for d in dirs for f in sorted(d.glob("*.json"))
                     if f not in provider_files and f.name != "ground_truth.json"]
    for d, f in patient_files:
        res_by_type: dict[str, list[dict]] = defaultdict(list)
        for r in _iter_resources(f):
            res_by_type[r["resourceType"]].append(r)
            if r["resourceType"] in ("Organization", "Practitioner"):
                refs.add(r)
        if not res_by_type.get("Patient"):
            continue
        p = res_by_type["Patient"][0]
        pid = p["id"]
        addr = (p.get("address") or [{}])[0]
        line = " ".join(addr.get("line", []))
        phone = next((t["value"] for t in p.get("telecom", []) if t.get("system") == "phone"), "")
        patients.append({
            "patient_id": pid, "name": _clean_name((p.get("name") or [{}])[0]), "gender": p.get("gender", ""),
            "birth_date": p.get("birthDate", ""), "city": addr.get("city", ""), "state": addr.get("state", ""),
            # Normalised address key: the graph uses it to spot shared addresses.
            "address_key": re.sub(r"\W+", " ", f"{line} {addr.get('city', '')}").strip().lower(),
            "phone": re.sub(r"\D", "", phone), "source": source_of[d],
        })
        bundles.append({"patient_id": pid, "path": str(f.relative_to(PATHS.root))})

        for e in res_by_type.get("Encounter", []):
            primary = referrer = None
            for part in e.get("participant", []):
                codes = {c.get("code") for t in part.get("type", []) for c in t.get("coding", [])}
                _, pr_id = refs.resolve(part.get("individual", {}).get("reference"))
                if "REF" in codes:
                    referrer = pr_id
                elif primary is None:
                    primary = pr_id
            _, org_id = refs.resolve(e.get("serviceProvider", {}).get("reference"))
            if org_id and org_id not in orgs:
                orgs[org_id] = {"org_id": org_id, "name": e["serviceProvider"].get("display", org_id),
                                "city": "", "kind": "Healthcare Provider"}
            for pr in (primary, referrer):
                if pr and pr not in practs:
                    practs[pr] = {"practitioner_id": pr, "reg_no": pr, "name": "Dr. (unknown)", "gender": ""}
            reason = ((e.get("reasonCode") or [{}])[0].get("coding") or [{}])[0]
            encounters.append({
                "encounter_id": e["id"], "patient_id": pid, "org_id": org_id, "practitioner_id": primary,
                "referrer_id": referrer, "encounter_class": e.get("class", {}).get("code", "AMB"),
                "type": _split_display((e.get("type") or [{}])[0].get("text", ""))[0],
                "reason_code": reason.get("code"), "start": _date(e.get("period", {}).get("start")),
                "end": _date(e.get("period", {}).get("end")),
            })

        for c in res_by_type.get("Condition", []):
            cod = (c.get("code", {}).get("coding") or [{}])[0]
            display, tag = _split_display(cod.get("display") or c.get("code", {}).get("text", ""))
            if tag not in _KEEP_TAGS:
                continue  # drop "(finding)", "(situation)" — social/admin noise in Synthea
            conditions.append({
                "condition_id": c["id"], "patient_id": pid,
                "encounter_id": refs.resolve(c.get("encounter", {}).get("reference"))[1],
                "code": cod.get("code"), "display": display,
                "onset": _date(c.get("onsetDateTime") or c.get("recordedDate")),
            })

        for cv in res_by_type.get("Coverage", []):
            plan = next((cl.get("value") for cl in cv.get("class", []) if cl.get("value") in PLAN_CODES), None)
            coverages.append({"coverage_id": cv["id"], "patient_id": pid,
                              "plan_code": plan or _plan_for(pid),
                              "start": _date(cv.get("period", {}).get("start")), "synthesized": False})

        for cl in res_by_type.get("Claim", []):
            ctype = (cl.get("type", {}).get("coding") or [{}])[0].get("code")
            if ctype == "pharmacy":
                continue  # medication fills are out of scope for this project
            enc_ref = next((en.get("reference") for it in cl.get("item", []) for en in it.get("encounter", [])), None)
            care = (cl.get("careTeam") or [{}])[0].get("provider", {}).get("reference")
            ins = (cl.get("insurance") or [{}])[0].get("coverage", {})
            total = cl.get("total", {})
            amount = float(total.get("value", 0.0))
            if total.get("currency", "USD") == "USD":
                amount *= USD_TO_INR
            items = [it.get("productOrService", {}) for it in cl.get("item", [])]
            proc = items[-1] if items else {}
            claims.append({
                "claim_id": cl["id"], "patient_id": pid,
                "encounter_id": refs.resolve(enc_ref)[1],
                "org_id": refs.resolve(cl.get("provider", {}).get("reference"))[1],
                "practitioner_id": refs.resolve(care)[1] if care else None,
                "coverage_ref": refs.resolve(ins.get("reference"))[1] if ins.get("reference") else None,
                "created": _date(cl.get("created") or cl.get("billablePeriod", {}).get("start")),
                "total_inr": round(amount, 2),
                "diagnosis_ids": "|".join(filter(None, (refs.resolve(dx.get("diagnosisReference", {}).get("reference"))[1]
                                                         for dx in cl.get("diagnosis", [])))),
                "procedure": _split_display(proc.get("text") or (proc.get("coding") or [{}])[0].get("display", ""))[0],
            })

    return _assemble(patients, orgs, practs, encounters, conditions, coverages, claims, bundles)


def _assemble(patients, orgs, practs, encounters, conditions, coverages, claims, bundles) -> dict[str, pd.DataFrame]:
    pat = pd.DataFrame(patients)
    enc = pd.DataFrame(encounters)
    cond = pd.DataFrame(conditions)
    cov = pd.DataFrame(coverages, columns=["coverage_id", "patient_id", "plan_code", "start", "synthesized"])
    clm = pd.DataFrame(claims)

    # ---- claim window: keep the last CLAIM_YEARS before the newest claim ------
    as_of = clm["created"].max()
    window_start = as_of - timedelta(days=365 * settings.claim_years)
    clm = clm[clm["created"] >= window_start].copy()

    # ---- coverage: synthesize for patients without a Coverage resource -------
    have = set(cov["patient_id"])
    synth = []
    for pid in pat["patient_id"]:
        if pid not in have:
            h = _stable_int(pid)
            synth.append({"coverage_id": f"cov-{pid}", "patient_id": pid,
                          "plan_code": _plan_for(pid),
                          # cover starts between 6 months before and ~30 months into the window
                          "start": window_start + timedelta(days=(h // 7) % 1080 - 180),
                          "synthesized": True})
    cov = pd.concat([cov, pd.DataFrame(synth)], ignore_index=True).drop_duplicates("patient_id")
    clm = clm.merge(cov[["patient_id", "coverage_id"]], on="patient_id", how="left").drop(columns="coverage_ref")

    # ---- encounters: attach class/practitioner; fill missing claim fields ----
    enc_cols = enc.set_index("encounter_id")
    clm["encounter_class"] = clm["encounter_id"].map(enc_cols["encounter_class"]).fillna("AMB")
    clm["practitioner_id"] = clm["practitioner_id"].fillna(clm["encounter_id"].map(enc_cols["practitioner_id"]))
    clm["org_id"] = clm["org_id"].fillna(clm["encounter_id"].map(enc_cols["org_id"]))

    # ---- primary diagnosis: claim.diagnosis -> encounter.reasonCode ---------
    cond_by_id = cond.set_index("condition_id")
    # First diagnosis date per (patient, code): that is the onset that matters
    # for pre-existing-disease rules, not the date of this particular visit.
    first_onset = cond.groupby(["patient_id", "code"])["onset"].min()
    first_display = cond.groupby("code")["display"].first()

    def primary_dx(row) -> tuple[str | None, str | None]:
        for cid in str(row.diagnosis_ids or "").split("|"):
            if cid in cond_by_id.index:
                c = cond_by_id.loc[cid]
                return c["code"], c["display"]
        rc = enc_cols["reason_code"].get(row.encounter_id) if row.encounter_id in enc_cols.index else None
        if rc and rc in first_display.index:
            return rc, first_display[rc]
        return None, None

    dx = clm.apply(primary_dx, axis=1, result_type="expand")
    clm["dx_code"], clm["dx_display"] = dx[0], dx[1]
    clm["dx_onset"] = [first_onset.get((p, c)) if c else None for p, c in zip(clm["patient_id"], clm["dx_code"])]
    clm = clm.drop(columns="diagnosis_ids")

    # Members of a hospitalisation-only scheme rarely file outpatient bills it
    # doesn't cover. Like the generator, keep ~30% of them so the D02 "not
    # covered" pattern still exists. Deterministic: decided by a claim-id hash.
    plan_of = clm["patient_id"].map(cov.set_index("patient_id")["plan_code"])
    unfiled = [
        (not PLANS[p].opd_covered and enc == "AMB" and category_for(dx) not in PLANS[p].daycare_categories
         and _stable_int(cid) % 10 < 7)
        for p, enc, dx, cid in zip(plan_of, clm["encounter_class"], clm["dx_display"], clm["claim_id"])
    ]
    clm = clm[[not u for u in unfiled]].copy()

    # ---- ground truth from the planter (labels only; detectors never see it) -
    clm["ring_id"], pat["ring_id"] = None, None
    gt_path = PATHS.planted_fhir / "ground_truth.json"
    if gt_path.exists():
        for ring in json.loads(gt_path.read_text())["rings"]:
            clm.loc[clm["claim_id"].isin(ring["claims"]), "ring_id"] = ring["ring_id"]
            pat.loc[pat["patient_id"].isin(ring["patients"]), "ring_id"] = ring["ring_id"]
    clm["is_planted_fraud"] = clm["ring_id"].notna().astype(int)

    # Keep only encounters/conditions relevant to the kept claims + all
    # conditions (needed for onset look-ups of chronic disease).
    enc = enc[enc["encounter_id"].isin(set(clm["encounter_id"]))]
    return {
        "patients": pat, "organizations": pd.DataFrame(orgs.values()),
        "practitioners": pd.DataFrame(practs.values()), "encounters": enc, "conditions": cond,
        "coverages": cov, "claims": clm, "patient_bundles": pd.DataFrame(bundles),
    }


def main() -> None:
    tables = parse()
    PATHS.processed.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(PATHS.processed / f"{name}.csv", index=False)
        print(f"  {name:<15} {len(df):>7,} rows")
    print(f"Tables written to {PATHS.processed}")


if __name__ == "__main__":
    main()
