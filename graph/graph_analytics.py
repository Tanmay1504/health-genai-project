"""
graph/graph_analytics.py — communities, graph features and fraud-ring candidates.

    python -m graph.graph_analytics                  # auto: Neo4j+GDS if up, else networkx
    python -m graph.graph_analytics --backend networkx

Outputs (consumed by brain/ and api/):
    data/processed/graph_features.csv   one row per claim, graph-derived features
    data/processed/fraud_rings.json     ranked ring candidates + subgraphs for the UI

Pipeline
--------
 1. COMMUNITIES. Louvain over the "collusion graph": patients, clinics,
    doctors, addresses and phones, joined by VISITED / SEEN_BY / WORKS_AT /
    LIVES_AT / HAS_PHONE / REFERRED_TO edges.
      backend "neo4j"    -> GDS procedures from fraud_queries.cypher (in-DB)
      backend "networkx" -> identical projection built from the CSVs (in-Python)
    Both backends return the same table (kind, id, community, pagerank). That
    is why the system still works on a laptop without Docker, and why the two
    backends are easy to compare.

 2. FEATURES. Per-entity signals (shared phone, doctor's clinic count,
    referral-loop membership, pre-auth-threshold structuring, claim velocity)
    are joined onto every claim. These are the *graph* features the XGBoost
    fraud model consumes, and the reason it beats a model trained on claim
    fields alone: the claim fields of a ring member look normal.

 3. RINGS. Each community gets a ring score from five pattern components.
    A ring usually shows 2-3 of the 5 patterns strongly, rather than all of
    them, so the score is the MEAN OF THE TOP-2 components: one strong pattern
    is not enough, and nothing needs to be strong everywhere.

 4. EVALUATION. Compare flagged patients to the planted answer key. This is
    the only place ground truth is read.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

import networkx as nx
import numpy as np
import pandas as pd

from core.config import PATHS, settings
from data.reference import PLANS
from graph.db import gds_available, neo4j_available
from graph.run_fraud_queries import run_all_patterns, run_named

RING_SCORE_THRESHOLD = 0.5
MIN_RING_PATIENTS = 4


# =============================================================================
# 1. Load tables
# =============================================================================
def _load() -> dict[str, pd.DataFrame]:
    p = PATHS.processed
    pat = pd.read_csv(p / "patients.csv", dtype={"phone": str})
    pat["phone"] = pat["phone"].fillna("")
    pat["address_key"] = pat["address_key"].fillna("")
    claims = pd.read_csv(p / "claims_labeled.csv", parse_dates=["created"])
    enc = pd.read_csv(p / "encounters.csv")
    return {"patients": pat, "claims": claims, "encounters": enc}


def collusion_edges(t: dict[str, pd.DataFrame]) -> list[tuple[str, str, str]]:
    """Same projection as `gds_project_collusion_graph`, built from CSVs.
    Node keys are "Kind:id" so ids from different tables can't collide.
    Returns unique (u, v, type) edges; `edge_weights` gives their weights."""
    return sorted(edge_weights(t))


def edge_weights(t: dict[str, pd.DataFrame]) -> dict[tuple[str, str, str], float]:
    """Edge -> interaction count (identity edges = 1), mirroring the `weight`
    property the Neo4j loader writes. Weighting stops one camouflage visit from
    tying a ring to a big legitimate hospital as strongly as its real activity."""
    enc, pat = t["encounters"], t["patients"]
    edges: dict[tuple[str, str, str], float] = defaultdict(float)
    for r in enc.itertuples():
        P = f"Patient:{r.patient_id}"
        if isinstance(r.org_id, str):
            edges[(P, f"Organization:{r.org_id}", "VISITED")] += 1
        if isinstance(r.practitioner_id, str):
            edges[(P, f"Practitioner:{r.practitioner_id}", "SEEN_BY")] += 1
            if isinstance(r.org_id, str):
                edges[(f"Practitioner:{r.practitioner_id}", f"Organization:{r.org_id}", "WORKS_AT")] += 1
            if isinstance(r.referrer_id, str) and r.referrer_id != r.practitioner_id:
                edges[(f"Practitioner:{r.referrer_id}", f"Practitioner:{r.practitioner_id}", "REFERRED_TO")] += 1
    for r in pat.itertuples():
        if r.address_key:
            edges[(f"Patient:{r.patient_id}", f"Address:{r.address_key}", "LIVES_AT")] = 1.0
        if r.phone:
            edges[(f"Patient:{r.patient_id}", f"Phone:{r.phone}", "HAS_PHONE")] = 1.0
    return dict(edges)


# =============================================================================
# 2. Community detection backends
# =============================================================================
def communities_networkx(weights: dict[tuple[str, str, str], float], seed: int) -> pd.DataFrame:
    G = nx.Graph()
    for (u, v, _), w in weights.items():
        # Undirected: a REFERRED_TO pair in both directions sums into one edge.
        G.add_edge(u, v, weight=G[u][v]["weight"] + w if G.has_edge(u, v) else w)
    comms = nx.community.louvain_communities(G, weight="weight", seed=seed)
    pr = nx.pagerank(G, weight="weight")
    rows = []
    for cid, members in enumerate(sorted(comms, key=len, reverse=True)):
        for node in members:
            kind, _, nid = node.partition(":")
            rows.append({"kind": kind, "id": nid, "community": cid, "pagerank": pr[node]})
    return pd.DataFrame(rows)


def communities_gds() -> pd.DataFrame:
    for step in ("gds_drop_projection", "gds_project_collusion_graph", "gds_louvain", "gds_pagerank", "gds_wcc"):
        res = run_named(step)
        print(f"  GDS {step:<30} {res[0] if res else ''}")
    df = pd.DataFrame(run_named("community_members"))
    run_named("gds_drop_projection")  # free the in-memory projection
    return df


def compute_communities(t, backend: str) -> tuple[pd.DataFrame, str]:
    if backend in ("auto", "neo4j") and neo4j_available() and gds_available():
        return communities_gds(), "neo4j-gds"
    if backend == "neo4j":
        raise SystemExit("Neo4j/GDS not reachable; use --backend networkx or start docker compose.")
    return communities_networkx(edge_weights(t), settings.seed), "networkx"


# =============================================================================
# 3. Feature engineering
# =============================================================================
def _claims_in_window(dates: pd.Series, days: int) -> np.ndarray:
    """For each claim: how many of this patient's claims fall in the preceding
    `days` (inclusive). searchsorted on sorted dates = O(n log n)."""
    order = np.argsort(dates.values)
    d = dates.values[order]
    lo = np.searchsorted(d, d - np.timedelta64(days, "D"), side="left")
    counts = np.arange(len(d)) - lo + 1
    out = np.empty_like(counts)
    out[order] = counts
    return out


def build_features(t, comm: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    pat, claims, enc = t["patients"], t["claims"].copy(), t["encounters"]

    # --- patient-level identity sharing ---------------------------------------
    phone_n = pat[pat.phone != ""].groupby("phone")["patient_id"].transform("count")
    pat["p_shared_phone"] = (phone_n - 1).reindex(pat.index).fillna(0).astype(int)
    addr_n = pat[pat.address_key != ""].groupby("address_key")["patient_id"].transform("count")
    pat["p_shared_address"] = (addr_n - 1).reindex(pat.index).fillna(0).astype(int)
    pat["p_distinct_clinics"] = pat["patient_id"].map(enc.groupby("patient_id")["org_id"].nunique()).fillna(0)

    # --- claim velocity ------------------------------------------------------
    claims = claims.sort_values(["patient_id", "created"])
    claims["p_claims_90d"] = claims.groupby("patient_id")["created"].transform(
        lambda s: pd.Series(_claims_in_window(s, 90), index=s.index))

    # --- doctor-level --------------------------------------------------------
    d_clinics = enc.groupby("practitioner_id")["org_id"].nunique()
    thr = claims["plan_code"].map({c: p.preauth_threshold_inr for c, p in PLANS.items()})
    claims["near_threshold"] = ((claims["total_inr"] >= 0.9 * thr) & (claims["total_inr"] < thr)).astype(int)
    d_near = claims.groupby("practitioner_id")["near_threshold"].mean()
    d_n = claims.groupby("practitioner_id").size()
    d_near = d_near.where(d_n >= 5, 0.0)  # too few claims to call it a pattern

    # Referral loops: a doctor inside a strongly-connected component of size >= 3
    # in the directed referral graph is, by definition, on a closed loop.
    ref = enc.dropna(subset=["referrer_id", "practitioner_id"])
    DG = nx.DiGraph()
    DG.add_edges_from(zip(ref["referrer_id"], ref["practitioner_id"]))
    in_loop = {n for scc in nx.strongly_connected_components(DG) if len(scc) >= 3 for n in scc}

    # --- clinic-level --------------------------------------------------------
    docs_by_org = enc.groupby("org_id")["practitioner_id"].apply(lambda s: set(s.dropna()))
    pats_by_org = enc.groupby("org_id")["patient_id"].apply(set)
    o_shared_doc_clinics, o_max_jacc = {}, {}
    orgs = list(docs_by_org.index)
    for a in orgs:
        o_shared_doc_clinics[a] = sum(1 for b in orgs if b != a and len(docs_by_org[a] & docs_by_org[b]) >= 2)
        o_max_jacc[a] = max((len(pats_by_org[a] & pats_by_org[b]) / len(pats_by_org[a] | pats_by_org[b])
                             for b in orgs if b != a), default=0.0)

    # --- community-level -----------------------------------------------------
    comm_of = {(r.kind, str(r.id)): r.community for r in comm.itertuples()}
    pr_of = {(r.kind, str(r.id)): r.pagerank for r in comm.itertuples()}
    pat["community"] = [comm_of.get(("Patient", p), -1) for p in pat["patient_id"]]

    # --- assemble per-claim feature table -------------------------------------
    pidx = pat.set_index("patient_id")
    f = pd.DataFrame({"claim_id": claims["claim_id"].values})
    f["community"] = claims["patient_id"].map(pidx["community"]).values
    f["p_shared_phone"] = claims["patient_id"].map(pidx["p_shared_phone"]).values
    f["p_shared_address"] = claims["patient_id"].map(pidx["p_shared_address"]).values
    f["p_distinct_clinics"] = claims["patient_id"].map(pidx["p_distinct_clinics"]).values
    f["p_claims_90d"] = claims["p_claims_90d"].values
    f["d_clinic_count"] = claims["practitioner_id"].map(d_clinics).fillna(0).values
    f["d_near_threshold_rate"] = claims["practitioner_id"].map(d_near).fillna(0).values
    f["d_in_referral_loop"] = claims["practitioner_id"].isin(in_loop).astype(int).values
    f["o_shared_doctor_clinics"] = claims["org_id"].map(o_shared_doc_clinics).fillna(0).values
    f["o_max_patient_jaccard"] = claims["org_id"].map(o_max_jacc).fillna(0).round(4).values
    f["o_pagerank"] = [pr_of.get(("Organization", o), 0.0) for o in claims["org_id"]]

    # Community aggregates: "how suspicious is the neighbourhood this claim sits in?"
    f["near_threshold"] = claims["near_threshold"].values
    f["patient_id"] = claims["patient_id"].values
    f["practitioner_id"] = claims["practitioner_id"].values
    grp = f.groupby("community")
    comm_stats = pd.DataFrame({
        "comm_patients": grp["patient_id"].nunique(),
        "comm_identity_rate": grp.apply(lambda g: ((g.p_shared_phone >= 2) | (g.p_shared_address >= 4)).mean()),
        "comm_velocity_rate": grp.apply(lambda g: (g.p_claims_90d >= 5).mean()),
        "comm_doc_mesh_rate": grp.apply(lambda g: (g.drop_duplicates("practitioner_id").d_clinic_count >= 3).mean()),
        "comm_referral_loop_rate": grp["d_in_referral_loop"].mean(),
        "comm_structuring_rate": grp["near_threshold"].mean(),
    })
    comps = ["comm_identity_rate", "comm_velocity_rate", "comm_doc_mesh_rate",
             "comm_referral_loop_rate", "comm_structuring_rate"]
    top2 = np.sort(comm_stats[comps].values, axis=1)[:, -2:]
    comm_stats["ring_score"] = top2.mean(axis=1).round(4)
    comm_stats.loc[comm_stats["comm_patients"] < MIN_RING_PATIENTS, "ring_score"] = 0.0
    f = f.merge(comm_stats, left_on="community", right_index=True, how="left")
    f = f.drop(columns=["near_threshold", "patient_id", "practitioner_id"])

    extras = {"patients": pat, "in_loop": in_loop, "comm_stats": comm_stats, "claims": claims,
              "d_clinics": d_clinics, "d_near": d_near}
    return f, extras


# =============================================================================
# 4. Ring candidates (for the UI) + evaluation
# =============================================================================
def _evidence(cs: pd.Series, members: dict, extras: dict, t) -> list[str]:
    ev = []
    pat = extras["patients"]
    mp = pat[pat.patient_id.isin(members["Patient"])]
    if cs.comm_identity_rate >= 0.3:
        top_phone = mp[mp.phone != ""].groupby("phone").size().sort_values(ascending=False)
        top_addr = mp[mp.address_key != ""].groupby("address_key").size().sort_values(ascending=False)
        if len(top_phone) and top_phone.iloc[0] >= 3:
            ev.append(f"{top_phone.iloc[0]} patients share phone …{top_phone.index[0][-4:]}")
        if len(top_addr) and top_addr.iloc[0] >= 3:
            ev.append(f"{top_addr.iloc[0]} patients registered at '{top_addr.index[0]}'")
    mesh_docs = [d for d in members["Practitioner"] if extras["d_clinics"].get(d, 0) >= 3]
    if mesh_docs:
        ev.append(f"{len(mesh_docs)} doctors each bill at 3+ clinics in this group")
    loop_docs = [d for d in members["Practitioner"] if d in extras["in_loop"]]
    if loop_docs:
        ev.append(f"{len(loop_docs)} doctors form a closed referral loop")
    if cs.comm_structuring_rate >= 0.3:
        ev.append(f"{cs.comm_structuring_rate:.0%} of claims are 90-99% of the pre-auth threshold")
    if cs.comm_velocity_rate >= 0.3:
        ev.append(f"{cs.comm_velocity_rate:.0%} of claims come from patients with 5+ claims in 90 days")
    return ev


def detect_rings(features: pd.DataFrame, comm: pd.DataFrame, extras: dict, t,
                 edges: list[tuple[str, str, str]]) -> list[dict]:
    cs_all = extras["comm_stats"]
    flagged = cs_all[cs_all.ring_score >= RING_SCORE_THRESHOLD].sort_values("ring_score", ascending=False)
    claims = extras["claims"]
    rings = []
    for rank, (cid, cs) in enumerate(flagged.iterrows(), start=1):
        mem = comm[comm.community == cid]
        members = {k: set(mem[mem.kind == k]["id"].astype(str)) for k in
                   ("Patient", "Organization", "Practitioner", "Address", "Phone")}
        ring_claims = claims[claims.patient_id.isin(members["Patient"])]
        rings.append({
            "ring_id": f"R{rank}",
            "community": int(cid),
            "ring_score": float(cs.ring_score),
            "components": {k.replace("comm_", "").replace("_rate", ""): round(float(cs[k]), 3) for k in
                           ("comm_identity_rate", "comm_velocity_rate", "comm_doc_mesh_rate",
                            "comm_referral_loop_rate", "comm_structuring_rate")},
            "evidence": _evidence(cs, members, extras, t),
            "counts": {"patients": len(members["Patient"]), "clinics": len(members["Organization"]),
                       "doctors": len(members["Practitioner"]), "claims": int(len(ring_claims)),
                       "billed_inr": round(float(ring_claims.total_inr.sum()), 2)},
            "patient_ids": sorted(members["Patient"]),
            "claim_ids": sorted(ring_claims.claim_id),
            "graph": _subgraph(members, edges, t),
        })
    return rings


def _subgraph(members: dict, edges, t, max_nodes: int = 300) -> dict:
    """Nodes/edges for the UI force graph. Identity nodes are kept only if
    >= 2 ring patients use them. A shared phone is the interesting part."""
    keep = {f"{k}:{i}" for k in ("Patient", "Organization", "Practitioner") for i in members[k]}
    ident_use: dict[str, int] = defaultdict(int)
    for u, v, typ in edges:
        if typ in ("LIVES_AT", "HAS_PHONE") and u in keep:
            ident_use[v] += 1
    keep |= {n for n, c in ident_use.items() if c >= 2}
    keep = set(sorted(keep)[:max_nodes])

    pat = t["patients"].set_index("patient_id")
    orgs = pd.read_csv(PATHS.processed / "organizations.csv").set_index("org_id")
    docs = pd.read_csv(PATHS.processed / "practitioners.csv").set_index("practitioner_id")

    def label(node: str) -> str:
        kind, _, i = node.partition(":")
        if kind == "Patient":
            return pat.name.get(i, i[:8]) if i in pat.index else i[:8]
        if kind == "Organization":
            return orgs.name.get(i, i[:8]) if i in orgs.index else i[:8]
        if kind == "Practitioner":
            return docs.name.get(i, i[:8]) if i in docs.index else i[:8]
        if kind == "Phone":
            return f"☎ …{i[-4:]}"
        return f"⌂ {i[:24]}"

    nodes = [{"id": n, "kind": n.split(":", 1)[0], "label": label(n)} for n in sorted(keep)]
    links = [{"source": u, "target": v, "type": typ} for u, v, typ in edges if u in keep and v in keep]
    return {"nodes": nodes, "links": links}


def evaluate(rings: list[dict]) -> dict:
    gt_path = PATHS.planted_fhir / "ground_truth.json"
    if not gt_path.exists():
        return {}
    truth = json.loads(gt_path.read_text())["rings"]
    true_pats = {p for r in truth for p in r["patients"]}
    flagged = {p for r in rings for p in r["patient_ids"]}
    tp = len(true_pats & flagged)
    per_ring = {r["ring_id"]: {g["ring_id"]: len(set(g["patients"]) & set(r["patient_ids"])) for g in truth}
                for r in rings}
    return {
        "patient_precision": round(tp / len(flagged), 3) if flagged else 0.0,
        "patient_recall": round(tp / len(true_pats), 3) if true_pats else 0.0,
        "overlap_with_planted_rings": per_ring,
    }


def main(backend: str) -> None:
    t = _load()
    print("Community detection ...")
    comm, used = compute_communities(t, backend)
    print(f"  backend={used}  communities={comm.community.nunique()}  nodes={len(comm)}")

    features, extras = build_features(t, comm)
    features.to_csv(PATHS.processed / "graph_features.csv", index=False)
    print(f"  graph_features.csv: {len(features):,} claims x {features.shape[1] - 1} features")

    edges = collusion_edges(t)
    rings = detect_rings(features, comm, extras, t, edges)
    pattern_hits = {}
    if used == "neo4j-gds":
        # Attach the raw Cypher pattern-query results as extra evidence.
        pattern_hits = {k: len(v) for k, v in run_all_patterns().items()}
    ev = evaluate(rings)
    out = {"backend": used, "threshold": RING_SCORE_THRESHOLD, "cypher_pattern_hits": pattern_hits,
           "evaluation": ev, "rings": rings}
    (PATHS.processed / "fraud_rings.json").write_text(json.dumps(out, indent=2, default=str))

    print(f"Flagged {len(rings)} ring candidate(s):")
    for r in rings:
        print(f"  {r['ring_id']} score={r['ring_score']:.2f} {r['counts']} | " + "; ".join(r["evidence"]))
    if ev:
        print(f"Evaluation vs planted answer key: precision={ev['patient_precision']} recall={ev['patient_recall']}")
        print(f"  overlap: {ev['overlap_with_planted_rings']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Communities, graph features and ring detection.")
    ap.add_argument("--backend", choices=["auto", "neo4j", "networkx"], default="auto")
    main(ap.parse_args().backend)
