// =============================================================================
// graph/fraud_queries.cypher — Cypher queries that surface fraud RINGS.
//
// How to use:
//   * Paste any block into Neo4j Browser (http://localhost:7474), replacing
//     $params with literals, OR
//   * python -m graph.run_fraud_queries    (runs them all with default params)
//
// Format: each query starts with a `// name:` line and ends with `;`.
// graph/run_fraud_queries.py parses this file, so the SQL-like text you read
// here is exactly what runs. There is no hidden second copy in Python.
//
// WHY graphs for fraud rings?
//   A single fraudulent claim often looks normal on its own: right codes,
//   plausible amount. A ring shows up in the *relationships*: the same phone
//   behind 10 "different" patients, the same 4 doctors across 3 new clinics, a
//   referral path that loops back to where it started. Those are multi-hop
//   patterns: painful as SQL self-joins, one line of MATCH in Cypher.
//
// Rule: no query may read a property starting with `_` (e.g. _truth_ring).
// Those are the planted answer key, used only for evaluation.
// =============================================================================


// name: shared_phone_clusters
// PATTERN: identity sharing. Many patients list one phone number AND those
// same patients visit the same clinics. Families share phones too, so we
// require >= $min_patients sharers (default 4) and >= 3 of them at one clinic.
MATCH (ph:Phone)<-[:HAS_PHONE]-(p:Patient)
WITH ph, collect(DISTINCT p) AS pts
WHERE size(pts) >= $min_patients
UNWIND pts AS p
MATCH (p)-[:VISITED]->(o:Organization)
WITH ph, pts, o, count(DISTINCT p) AS sharers_here
WHERE sharers_here >= 3
RETURN ph.number                         AS phone,
       size(pts)                         AS patients_sharing_phone,
       collect(o.name + ' (' + toString(sharers_here) + ' of them)') AS clinics_they_use,
       [x IN pts | x.id]                 AS patient_ids
ORDER BY patients_sharing_phone DESC;


// name: shared_address_clusters
// PATTERN: many "patients" registered at one address, which is classic
// phantom-patient enrolment. Households (2-4 people) are normal, so the
// threshold is >= $min_patients (default 5) with >= 15 claims between them.
MATCH (a:Address)<-[:LIVES_AT]-(p:Patient)
WITH a, collect(DISTINCT p) AS pts
WHERE size(pts) >= $min_patients
UNWIND pts AS p
OPTIONAL MATCH (p)-[:FILED]->(c:Claim)
WITH a, pts, count(c) AS claims, sum(c.totalInr) AS billed
WHERE claims >= 15
RETURN a.key AS address, size(pts) AS patients_at_address, claims,
       round(billed) AS total_billed_inr, [x IN pts | x.id] AS patient_ids
ORDER BY patients_at_address DESC;


// name: clinic_doctor_mesh
// PATTERN: collusion mesh. Two clinics share >= 2 doctors AND >= 5 patients.
// Legit hospitals share the odd visiting consultant; a ring shares its whole
// roster, because it is really one operation billing under several names to
// stay below per-provider audit thresholds.
MATCH (o1:Organization)<-[:WORKS_AT]-(d:Practitioner)-[:WORKS_AT]->(o2:Organization)
WHERE o1.id < o2.id                       // each unordered pair once
WITH o1, o2, collect(DISTINCT d) AS shared_docs
WHERE size(shared_docs) >= 2
MATCH (o1)<-[:VISITED]-(p:Patient)-[:VISITED]->(o2)
WITH o1, o2, shared_docs, count(DISTINCT p) AS shared_patients
WHERE shared_patients >= 5
RETURN o1.name AS clinic_a, o2.name AS clinic_b,
       [d IN shared_docs | d.name] AS shared_doctors,
       shared_patients
ORDER BY shared_patients DESC;


// name: referral_cycles
// PATTERN: kickback loop. Doctor A refers to B, B to C, ... and the chain
// returns to A. Real referral graphs are mostly acyclic: a GP refers
// UP to a specialist. A closed loop means doctors are passing patients
// around to generate billable visits.
// `*3..6` bounds the search; unbounded var-length paths explode combinatorially.
MATCH path = (d:Practitioner)-[:REFERRED_TO*3..6]->(d)
WITH d, path, [n IN nodes(path) | n.id] AS ids
// The same loop is found once per member as the starting point. Keep only the
// rotation that starts at the smallest id, so each cycle is reported once.
WHERE d.id = reduce(m = head(ids), x IN ids | CASE WHEN x < m THEN x ELSE m END)
RETURN [n IN nodes(path) | n.name]                          AS loop,
       length(path)                                         AS hops,
       reduce(s = 0, r IN relationships(path) | s + r.count) AS referrals_in_loop
ORDER BY referrals_in_loop DESC;


// name: claim_velocity
// PATTERN: bursty billing. A patient with >= 5 claims inside 90 days spread
// over >= 2 clinics. Chronic patients do have many visits, but usually at one
// clinic and spread over the year.
MATCH (p:Patient)-[:FILED]->(c:Claim)-[:BILLED_BY]->(o:Organization)
WITH p, c, o ORDER BY c.created
WITH p, collect(c) AS cs, collect(DISTINCT o.name) AS clinics
WHERE size(cs) >= 5 AND size(clinics) >= 2
UNWIND range(0, size(cs) - 5) AS i
WITH p, clinics, cs, cs[i] AS first, cs[i + 4] AS fifth
WHERE duration.inDays(first.created, fifth.created).days <= 90
WITH p, clinics, cs, min(duration.inDays(first.created, fifth.created).days) AS tightest_5_claim_window_days
RETURN p.id AS patient_id, p.name AS patient, size(cs) AS claims, clinics,
       tightest_5_claim_window_days,
       round(reduce(s = 0.0, c IN cs | s + c.totalInr)) AS total_billed_inr
ORDER BY tightest_5_claim_window_days ASC, claims DESC;


// name: threshold_structuring
// PATTERN: amounts deliberately kept just under the pre-authorisation trigger
// (90-100% of the plan's threshold), so no insurer reviewer looks at them.
// $thresholds is a map {planCode: thresholdInr} passed from data/reference.py.
// The dynamic map lookup `$thresholds[c.planCode]` is valid Cypher.
MATCH (d:Practitioner)<-[:RENDERED_BY]-(c:Claim)
WITH d, c, toFloat($thresholds[c.planCode]) AS t
WITH d, count(c) AS n,
     sum(CASE WHEN c.totalInr >= 0.9 * t AND c.totalInr < t THEN 1 ELSE 0 END) AS near
WHERE n >= 5 AND toFloat(near) / n >= 0.5
RETURN d.name AS doctor, d.id AS practitioner_id, n AS claims,
       near AS claims_just_below_threshold, round(100.0 * near / n, 1) AS pct
ORDER BY pct DESC;


// =============================================================================
// Graph Data Science (GDS). These need the GDS plugin, which docker-compose
// installs. graph/graph_analytics.py runs them automatically; they are listed
// here so you can run them by hand too.
// =============================================================================

// name: gds_drop_projection
// GDS algorithms run on an in-memory PROJECTION, not on the stored graph.
CALL gds.graph.drop('collusion', false) YIELD graphName
RETURN graphName;


// name: gds_project_collusion_graph
// Project only the "who-is-connected-to-whom" layer: people, providers and
// identities, joined by the derived shortcut relationships. Claims/Encounters
// are left out on purpose: with thousands of them, every patient would look
// central and the community structure would be noise.
// UNDIRECTED, because collusion is mutual: who-referred-whom matters for the
// cycle query above, not for "which group do you belong to".
// WEIGHTED by interaction count (set by the loader). Without weights, one
// camouflage visit to a big legit hospital counts as much as eight visits to
// a ring clinic, and Louvain can absorb a whole ring into that hospital's community.
CALL gds.graph.project(
  'collusion',
  ['Patient', 'Organization', 'Practitioner', 'Address', 'Phone'],
  {
    VISITED:     {orientation: 'UNDIRECTED', properties: 'weight'},
    SEEN_BY:     {orientation: 'UNDIRECTED', properties: 'weight'},
    WORKS_AT:    {orientation: 'UNDIRECTED', properties: 'weight'},
    LIVES_AT:    {orientation: 'UNDIRECTED', properties: 'weight'},
    HAS_PHONE:   {orientation: 'UNDIRECTED', properties: 'weight'},
    REFERRED_TO: {orientation: 'UNDIRECTED', properties: 'weight'}
  }
) YIELD graphName, nodeCount, relationshipCount
RETURN graphName, nodeCount, relationshipCount;


// name: gds_louvain
// Louvain finds groups that are more densely connected inside than outside.
// A ring is exactly that: its patients, doctors and clinics mostly connect to
// each other. concurrency 1 makes the result deterministic.
CALL gds.louvain.write('collusion', {writeProperty: 'community', relationshipWeightProperty: 'weight', concurrency: 1})
YIELD communityCount, modularity
RETURN communityCount, modularity;


// name: gds_pagerank
// PageRank = how "central" a node is. Used as an ML feature: a tiny new clinic
// with hub-like centrality inside its community is suspicious.
CALL gds.pageRank.write('collusion', {writeProperty: 'pagerank', relationshipWeightProperty: 'weight', concurrency: 1})
YIELD nodePropertiesWritten
RETURN nodePropertiesWritten;


// name: gds_wcc
// Weakly connected components. Kept for teaching: thanks to the camouflage
// visits, the rings are NOT separate components (WCC fails to isolate them),
// while Louvain succeeds. Compare the two properties in the Browser.
CALL gds.wcc.write('collusion', {writeProperty: 'component', concurrency: 1})
YIELD componentCount
RETURN componentCount;


// name: community_members
// Read back the community/pagerank of every node in the projection.
MATCH (n)
WHERE n.community IS NOT NULL
RETURN labels(n)[0] AS kind,
       coalesce(n.id, n.key, n.number) AS id,
       n.community AS community,
       coalesce(n.pagerank, 0.0) AS pagerank;
