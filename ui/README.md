# ui/: Next.js front-end

## What it does
Six views, one per capability. All are client components that call the API through `lib/api.ts`.

| Route | File | Shows |
|---|---|---|
| `/` | `app/page.tsx` | All claims with denial-risk and fraud meters; **Run** sends a claim through the orchestrator and shows the route taken |
| `/preauth` | `app/preauth/page.tsx` | Patient → encounter → pre-auth draft; FHIR fields, rule-engine check, LLM justification with citations, FHIR `Claim` JSON |
| `/fraud` | `app/fraud/page.tsx` | Ring cards with evidence, pattern scores, a force-directed ring network (`components/ForceGraph.tsx`, d3-force + SVG) |
| `/denial` | `app/denial/page.tsx` | P(denied), top reason codes, signed SHAP contribution bars |
| `/appeal` | `app/appeal/page.tsx` | Generated letter with clickable citations, the sources, grounds and agent trace |
| `/audit` | `app/audit/page.tsx` | Decision log with hash-chain status; click a row for inputs, retrieval and output |

Supporting files: `lib/api.ts` (typed fetch client and response types), `lib/useQueryParam.ts` (keeps `?claim=` in the URL), `components/` (Nav, Meter, ClaimPicker, ForceGraph), `app/globals.css` (light and dark theme tokens).

## How it fits the whole
The browser only calls same-origin `/api/*`, and `next.config.mjs` **rewrites** those calls to FastAPI (`API_URL`, default `http://localhost:8000`). That avoids CORS and keeps the backend address server-side. `proxyTimeout` is raised to 5 minutes because local-LLM appeals can be slow.

## How to run
```bash
cd ui
npm install
npm run dev            # http://localhost:3000  (API must be running on :8000)
npm run build && npm start     # production mode
npm run typecheck
```

## How to verify
- The header badges on `/` show the LLM provider, Neo4j status and models trained.
- `/fraud` shows two rings with precision and recall 1.0 against the answer key (fallback dataset).
- On `/` pick **Wrongfully denied**, click **Run** on a D03 claim, then follow "Open the appeal letter".
- `/audit` shows a green "Hash chain intact" banner.
