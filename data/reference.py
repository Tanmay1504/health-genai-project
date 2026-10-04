"""
data/reference.py — reference data + the (fictional) insurer's rulebook.

This is the single source of truth for:
  1. DENIAL REASON CODES used across the system (labels, model classes, UI).
  2. CLINICAL CATEGORIES and how a free-text diagnosis maps onto them.
  3. INSURANCE PLANS: sum insured, waiting periods, pre-auth thresholds,
     sub-limits, exclusions. The markdown policy wordings in
     data/knowledge/policies/ describe EXACTLY these numbers, so what the
     deterministic rule engine enforces and what the RAG agents cite agree.
  4. `evaluate_policy()`: a deterministic rule engine. It is used to
       * simulate insurer adjudication (data/simulate_adjudication.py),
       * decide whether a predicted denial would be *wrongful* (orchestrator),
       * give the pre-auth agent hard facts before the LLM writes anything.

WHY a deterministic engine next to an LLM?
  Coverage arithmetic ("is ₹62,000 over the ₹25,000 cataract sub-limit?") must
  never be left to a language model. The LLM's job is to *explain and cite*;
  this module's job is to *decide the hard facts*.

Everything here is synthetic. Insurer and plan names are fictional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# =============================================================================
# 1. Denial reason codes (insurer-style, fictional numbering)
# =============================================================================
REASON_CODES: dict[str, str] = {
    "D01": "Pre-authorisation not obtained",
    "D02": "Treatment not covered under the plan",
    "D03": "Pre-existing disease waiting period not completed",
    "D04": "Incomplete or missing documentation",
    "D05": "Claimed amount exceeds the plan sub-limit",
    "D06": "Claim submitted after the permitted time limit",
    "D07": "Suspected fraud / misrepresentation",
}
APPROVED = "APPROVED"

# =============================================================================
# 2. Clinical categories
# =============================================================================
CATEGORIES = [
    "general_medicine", "diabetes_metabolic", "cardiac", "respiratory", "orthopedic",
    "ophthalmology", "maternity", "surgical", "renal", "neurology",
]

# Ordered: first match wins. "Diabetic retinopathy" must hit ophthalmology
# before the generic "diabet" rule, hence specific organ systems come first.
_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("maternity", ("pregnan", "preeclampsia", "miscarriage", "fetus", "delivery", "antepartum")),
    ("ophthalmology", ("cataract", "glaucoma", "retinopathy", "macular", "eye")),
    ("neurology", ("stroke", "cerebr", "epilep", "seizure", "alzheimer", "dementia", "migraine", "concussion")),
    ("renal", ("kidney", "renal", "nephro")),
    ("cardiac", ("heart", "myocard", "coronary", "atrial", "cardiac", "angina", "hypertens")),
    ("respiratory", ("pneumon", "bronch", "asthma", "pulmonary", "copd", "emphysema", "respiratory", "sinusitis", "pharyngitis")),
    ("orthopedic", ("fracture", "osteoarth", "sprain", "joint", "osteopor", "back pain", "knee", "hip")),
    ("surgical", ("appendic", "gallstone", "cholecyst", "hernia", "polyp", "laceration")),
    ("diabetes_metabolic", ("diabet", "hyperglyc", "hyperlipid", "obes", "metabolic", "lipid")),
]

_CHRONIC_KEYWORDS = (
    "chronic", "diabet", "hypertens", "asthma", "osteoarth", "atrial fib", "hyperlipid", "obes",
    "kidney disease", "heart failure", "copd", "emphysema", "osteopor", "alzheimer", "epilep",
    "coronary", "rheumat", "hypothyroid", "dementia",
)


def _text(v) -> str:
    # pandas gives NaN (a float) for claims without a diagnosis (common in Synthea).
    return v.lower() if isinstance(v, str) else ""


def category_for(diagnosis_display: str | None) -> str:
    text = _text(diagnosis_display)
    for cat, keywords in _CATEGORY_KEYWORDS:
        if any(k in text for k in keywords):
            return cat
    return "general_medicine"


def is_chronic(diagnosis_display: str | None) -> bool:
    text = _text(diagnosis_display)
    return any(k in text for k in _CHRONIC_KEYWORDS)


# Catalog used by the fallback generator and the fraud planter.
# (SNOMED code, display, encounter class, procedure code, procedure display, cost range INR)
# Procedure codes are illustrative; costs are rough synthetic Indian private-sector figures.
CONDITION_CATALOG: list[dict] = [
    dict(code="44054006", display="Diabetes mellitus type 2", enc="AMB", proc=("43396009", "Hemoglobin A1c measurement"), cost=(1500, 6000), weight=10),
    dict(code="38341003", display="Essential hypertension", enc="AMB", proc=("46973005", "Blood pressure monitoring"), cost=(800, 3000), weight=12),
    dict(code="15777000", display="Prediabetes", enc="AMB", proc=("43396009", "Hemoglobin A1c measurement"), cost=(800, 2500), weight=6),
    dict(code="55822004", display="Hyperlipidemia", enc="AMB", proc=("252150008", "Lipid panel"), cost=(700, 2500), weight=6),
    dict(code="195662009", display="Acute viral pharyngitis", enc="AMB", proc=("117015009", "Throat culture"), cost=(600, 2500), weight=10),
    dict(code="10509002", display="Acute bronchitis", enc="AMB", proc=("399208008", "Chest X-ray"), cost=(1200, 4000), weight=8),
    dict(code="444814009", display="Viral sinusitis", enc="AMB", proc=("11429006", "Consultation"), cost=(500, 2000), weight=8),
    dict(code="65363002", display="Otitis media", enc="AMB", proc=("11429006", "Consultation"), cost=(500, 1500), weight=5),
    dict(code="195967001", display="Asthma", enc="AMB", proc=("23426006", "Measurement of respiratory function"), cost=(1500, 5000), weight=5),
    dict(code="233604007", display="Pneumonia", enc="IMP", proc=("32485007", "Hospital admission"), cost=(45000, 180000), weight=3),
    dict(code="22298006", display="Myocardial infarction", enc="IMP", proc=("415070008", "Percutaneous coronary intervention"), cost=(180000, 450000), weight=2),
    dict(code="49436004", display="Atrial fibrillation", enc="AMB", proc=("29303009", "Electrocardiographic procedure"), cost=(1000, 4000), weight=3),
    dict(code="230690007", display="Cerebrovascular accident (stroke)", enc="IMP", proc=("32485007", "Hospital admission"), cost=(120000, 400000), weight=1),
    dict(code="239873007", display="Osteoarthritis of knee", enc="IMP", proc=("609588000", "Total knee replacement"), cost=(150000, 350000), weight=2),
    dict(code="65966004", display="Fracture of forearm", enc="EMER", proc=("33496007", "Closed reduction of fracture"), cost=(25000, 90000), weight=2),
    dict(code="193570009", display="Cataract", enc="AMB", proc=("110473004", "Cataract extraction (phacoemulsification)"), cost=(18000, 65000), weight=3),
    dict(code="74400008", display="Appendicitis", enc="IMP", proc=("80146002", "Appendectomy"), cost=(60000, 160000), weight=2),
    dict(code="433144002", display="Chronic kidney disease stage 3", enc="AMB", proc=("302497006", "Hemodialysis"), cost=(15000, 45000), weight=2),
    dict(code="72892002", display="Normal pregnancy", enc="IMP", proc=("11466000", "Cesarean section"), cost=(70000, 150000), weight=2),
]

# =============================================================================
# 3. Insurance plans (fictional insurer: "Suraksha General Health Insurance")
# =============================================================================
@dataclass(frozen=True)
class Plan:
    code: str
    insurer: str
    name: str
    policy_doc: str                       # file in data/knowledge/policies/
    sum_insured_inr: int
    ped_waiting_months: int               # pre-existing disease waiting period
    preauth_threshold_inr: int            # any claim above this needs pre-auth
    preauth_for_inpatient: bool           # every inpatient (IMP) stay needs pre-auth
    opd_covered: bool                     # are outpatient consultations covered?
    daycare_categories: frozenset[str]    # outpatient categories covered even if OPD is not
    excluded_categories: frozenset[str] = frozenset()
    sublimits_inr: dict[str, int] = field(default_factory=dict)
    submission_days: int = 30             # claim must be filed within N days


PLANS: dict[str, Plan] = {
    "SGH-SILVER": Plan(
        code="SGH-SILVER",
        insurer="Suraksha General Health Insurance Ltd. (fictional)",
        name="Suraksha Silver Individual Health Plan",
        policy_doc="suraksha_silver_policy.md",
        sum_insured_inr=300_000,
        ped_waiting_months=36,
        preauth_threshold_inr=25_000,
        preauth_for_inpatient=True,
        opd_covered=True,
        daycare_categories=frozenset({"ophthalmology", "surgical", "renal"}),
        excluded_categories=frozenset({"maternity"}),
        sublimits_inr={"ophthalmology": 25_000, "orthopedic": 150_000, "cardiac": 200_000},
    ),
    "SGH-GOLD": Plan(
        code="SGH-GOLD",
        insurer="Suraksha General Health Insurance Ltd. (fictional)",
        name="Suraksha Gold Family Floater Plan",
        policy_doc="suraksha_gold_policy.md",
        sum_insured_inr=1_000_000,
        ped_waiting_months=24,
        preauth_threshold_inr=50_000,
        preauth_for_inpatient=True,
        opd_covered=True,
        daycare_categories=frozenset({"ophthalmology", "surgical", "renal"}),
        sublimits_inr={"ophthalmology": 60_000, "maternity": 100_000},
    ),
    "JAC-BASIC": Plan(
        code="JAC-BASIC",
        insurer="Jan Arogya Cover Trust (fictional government-style scheme)",
        name="Jan Arogya Cover — Basic Hospitalisation Package",
        policy_doc="jan_arogya_cover_policy.md",
        sum_insured_inr=500_000,
        ped_waiting_months=0,             # pre-existing diseases covered from day one
        preauth_threshold_inr=20_000,
        preauth_for_inpatient=True,
        opd_covered=False,                # hospitalisation scheme: no OPD consultations
        daycare_categories=frozenset({"ophthalmology", "surgical", "renal"}),
        sublimits_inr={"ophthalmology": 30_000, "orthopedic": 200_000},
    ),
}
PLAN_CODES = list(PLANS)


# =============================================================================
# 4. Deterministic policy rule engine
# =============================================================================
def months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) - (1 if end.day < start.day else 0)


def requires_preauth(plan: Plan, encounter_class: str, amount_inr: float) -> bool:
    return (plan.preauth_for_inpatient and encounter_class == "IMP") or amount_inr > plan.preauth_threshold_inr


def is_outpatient_covered(plan: Plan, encounter_class: str, category: str) -> bool:
    if encounter_class in ("IMP", "EMER"):
        return True
    return plan.opd_covered or category in plan.daycare_categories


def evaluate_policy(claim: dict) -> list[dict]:
    """Return the list of policy rules this claim violates (empty = compliant).

    `claim` keys used: plan_code, category, encounter_class, total_inr,
    preauth_obtained, chronic, dx_onset (date|None), coverage_start (date),
    claim_date (date), docs_complete, days_to_submit.

    Each violation: {"code": "D0x", "reason": ..., "detail": ...} where
    `detail` is a human-readable sentence with the actual numbers, which the
    agents and the UI show verbatim.
    """
    plan = PLANS[claim["plan_code"]]
    cat, enc, amt = claim["category"], claim["encounter_class"], float(claim["total_inr"])
    out: list[dict] = []

    def add(code: str, detail: str) -> None:
        out.append({"code": code, "reason": REASON_CODES[code], "detail": detail})

    if cat in plan.excluded_categories:
        add("D02", f"Category '{cat}' is excluded under {plan.name}.")
    elif not is_outpatient_covered(plan, enc, cat):
        add("D02", f"Outpatient '{cat}' care is not covered under {plan.name} (hospitalisation-only plan).")

    if requires_preauth(plan, enc, amt) and not claim.get("preauth_obtained", False):
        add("D01", f"Pre-authorisation required (inpatient or amount ₹{amt:,.0f} > ₹{plan.preauth_threshold_inr:,}) but not obtained.")

    onset, cov_start, cdate = claim.get("dx_onset"), claim["coverage_start"], claim["claim_date"]
    if claim.get("chronic") and onset is not None and onset < cov_start and plan.ped_waiting_months > 0:
        served = months_between(cov_start, cdate)
        if served < plan.ped_waiting_months:
            add("D03", f"Condition diagnosed {onset} before cover began {cov_start}; "
                       f"{served} of {plan.ped_waiting_months} waiting months served.")

    if cat in plan.sublimits_inr and amt > plan.sublimits_inr[cat]:
        add("D05", f"₹{amt:,.0f} exceeds the {cat} sub-limit of ₹{plan.sublimits_inr[cat]:,}.")

    if not claim.get("docs_complete", True):
        add("D04", "Mandatory documents (discharge summary / itemised bill / investigation reports) missing.")

    if claim.get("days_to_submit", 0) > plan.submission_days:
        add("D06", f"Submitted after {claim['days_to_submit']} days; limit is {plan.submission_days} days.")
    return out
