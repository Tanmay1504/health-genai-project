# data/: synthetic patients, planted fraud, tidy tables, insurer simulation

## What it does
Produces every dataset the rest of the system uses, entirely synthetic:

| File | Role |
|---|---|
| `fetch_synthea.sh` | Downloads Synthea and generates FHIR R4 patient bundles (Java 17+). |
| `generate_synthetic_fhir.py` | **Fallback** generator: Synthea-*shaped* FHIR with Indian-context names and cities, households, chronic disease, inpatient stays. No Java needed. |
| `fhir_builders.py` | Small helpers that emit FHIR resources (Patient, Organization, Practitioner, Encounter, Condition, Coverage, Claim) in Synthea's reference style. |
| `synthetic_names.py` | Fake names, cities and facility names. |
| `plant_fraud_rings.py` | Injects two fraud rings **as FHIR**, plus an answer key `planted_fhir/ground_truth.json`. |
| `parse_fhir.py` | Flattens all FHIR into `processed/*.csv` (patients, organizations, practitioners, encounters, conditions, coverages, claims, patient_bundles). |
| `reference.py` | **Single source of truth**: denial reason codes, clinical categories, the three fictional plans, and the deterministic policy rule engine `evaluate_policy()`. |
| `simulate_adjudication.py` | Simulates the insurer's decisions: rules + leniency + a deliberate bug. Writes `processed/claims_labeled.csv`. |
| `knowledge/policies/*.md` | Fictional policy wordings (RAG corpus). Their numbers match `reference.py`. |
| `knowledge/clinical/*.md` | Illustrative clinical guidance (RAG corpus). Not medical advice. |

## How it fits the whole
```
Synthea or generator ─▶ plant rings ─▶ parse_fhir ─▶ simulate_adjudication ─▶ graph/, brain/, api/
                                                   reference.py ──▶ used by simulate, preauth, appeal, orchestrator
knowledge/ ─▶ core/rag.py ─▶ preauth/ + appeal/
```

### The two planted rings
| Ring | Pattern | Graph signature |
|---|---|---|
| RING-A | Identity-sharing phantom billing: 3 new clinics, 4 shared doctors, 14 patients on 2 addresses, 10 of them on one phone, 5–8 high-value claims each in ~75 days | dense Patient–Clinic–Doctor block; many Patients → one Phone/Address |
| RING-B | Referral kickback loop + threshold structuring: 5 doctors refer in a cycle D0→D1→…→D0; bills at 90–99% of the pre-auth threshold | directed cycle in `REFERRED_TO`; amounts piled just under the threshold |

Each ring patient also makes one **camouflage** visit to a legitimate hospital, so the rings are not trivially disconnected.

### The simulated insurer
1. **Rules**: `evaluate_policy()` (pre-auth, exclusions, PED waiting period, sub-limits, documents, filing delay).
2. **Leniency**: about 8% of rule-violating claims are approved anyway.
3. **Bug (the appeal story)**: for chronic conditions first diagnosed *after* the policy began, the insurer still applies the PED waiting period about 60% of the time. Per the policy definition (clause 2.2) that is wrong. These claims are labeled `denial_wrongful=1`.

### Indian-context adaptations
- Synthea's USD totals are scaled by `USD_TO_INR = 4`: the ₹83/$ FX rate times Indian private-care prices being roughly 1/20 of US prices. A typical Synthea outpatient visit lands near ₹10,000.
- Synthea patients get one of the fictional Indian plans, assigned deterministically from a hash of the patient id.

## How to run
```bash
bash data/fetch_synthea.sh 250 42            # or: python -m data.generate_synthetic_fhir --patients 400
python -m data.plant_fraud_rings
python -m data.parse_fhir
python -m data.simulate_adjudication
```

## How to verify
- `parse_fhir` prints row counts for every table. Claims should be in the thousands, and none of the tables should be empty.
- `simulate_adjudication` prints the denial rate (expect 25–40%), the wrongful-denial count (> 0) and the reason distribution.
- `planted_fhir/ground_truth.json` lists 2 rings (14 + 10 patients).
- Spot-check one claim: `python -c "import pandas as pd; print(pd.read_csv('data/processed/claims_labeled.csv').iloc[0])"`.

Generated folders (`synthea_output/`, `generated_fhir/`, `planted_fhir/`, `processed/`, `chroma/`, `tools/`) are git-ignored. `knowledge/` is committed because it is text we wrote, not patient data.
