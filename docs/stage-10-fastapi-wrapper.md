# Stage 10 — FastAPI Wrapper (in plain language)

This document explains what Stage 10 added: a small FastAPI app (`api/main.py`) that
wraps the Part-A detection engine (Stages 1-9) as six HTTP endpoints, so a future React
frontend (Part B, Stages 11-13) has something real to call instead of a terminal script.
Nothing about *detection* changed in this stage — no new scoring, no new features, no new
agent behavior. This stage is purely plumbing: take what already works when you run
`make demo` from a terminal, and make it reachable over HTTP as JSON.

## 1. What problem is this trying to solve?

Every earlier stage produces useful output, but only if you run a Python script and read
what it prints (or opens a PNG). A web dashboard needs that same information as JSON,
on demand, over HTTP — and it needs it to stay *consistent* across several different
requests (the graph the user sees, the ring list they click through, and the metrics
page they check should all describe the same run, not three different random re-runs of
the pipeline).

PLAN.md §B3 specifies six endpoints. The one rule that matters most for this stage:
**no detection logic lives in the API.** Every endpoint below is a thin wrapper that
calls existing Stage 3-9 code and reshapes the result into JSON — if you compare
`api/main.py` to `demo/run_demo.py`, you'll see the same function calls
(`build_graph()`, `detect_communities()`, `score_communities()`, `investigate_community()`,
`decide_action()`), just returned as JSON instead of printed to a terminal.

## 2. The in-memory run cache

`POST /api/run` builds the graph, detects communities, and scores every community once,
then stashes the result in a single Python variable at module level (`_RUN`). Every `GET`
endpoint after that reads from this same cached object instead of recomputing anything.

This matters for two reasons:
1. **Speed.** Rebuilding the graph and re-scoring ~900 communities takes a few seconds;
   doing that on every page load would make the UI feel sluggish for no reason.
2. **Consistency.** If `/api/graph` and `/api/rings` each silently re-ran the pipeline
   independently, they could theoretically disagree slightly (though this pipeline is
   deterministic given the same data, so in practice they wouldn't — but caching makes
   that guarantee structural rather than incidental).

This is intentionally *one* global run, not a dictionary of many runs by id — the task
spec asks for "cache the result in memory" for a single local demo tool, not a multi-user
server. A `run_id` (a short random hex string) is still generated and returned everywhere
so the frontend has something stable to display, and any `GET` endpoint called before the
first `POST /api/run` returns HTTP 409 ("no run yet") instead of crashing.

## 3. The six endpoints

### `POST /api/run`
Runs `build_graph()` → `detect_communities()` → `score_communities()` (Stages 3-5),
caches the result, returns a `run_id` plus quick top-line stats (node/edge counts,
number of communities, any giant-blob flags). Deliberately does **not** eagerly
investigate every community with the LLM agent — that would mean a real OpenAI API call
per community (900+) on every single click of "Run detection," which is slow and costs
money for communities nobody will ever look at. Investigation happens lazily, per-ring,
the first time someone actually opens that ring's case file.

### `GET /api/graph`
Returns nodes + edges shaped for `react-force-graph`: `{id, type, cluster_id,
suspicious, risk}` per node, `{source, target, type, weight}` per edge. Restricted to
customers who belong to a community of 3+ members (plus the entities they directly
share) — including every singleton pincode/IP-subnet node from the full ~10,000-node
bipartite graph would make the visualization too dense to be useful, and those nodes
belong to customers not in any interesting cluster anyway.

### `GET /api/rings`
The ranked list: `{id, size, score, rupee_risk, status}` per community (score ≥ some
minimum size), sorted by ring-likelihood score. `rupee_risk` reuses
`agent/policy.py`'s own convention (`size × chargeback_cost`) so the number here matches
what a case file would report for the same community. `status` is `suspicious` /
`cleared` / `unlabeled` — a display label derived from ground truth for annotation only
(exactly like every eval script already does), never fed back into the score itself.

### `GET /api/rings/{id}`
The case file for one community: members, shared entities, the agent's evidence and
reasoning, the policy's bounded action and confidence, plus an evidence subgraph (a
small nodes+edges JSON blob, same shape as `/api/graph`, scoped to just that community
and its shared entities). If a case file already exists on disk
(`agent/case_files/community_<id>.json`, from an earlier `make investigate`/`make demo`
run or an earlier hit on this same endpoint), it's served straight from disk — instant,
no LLM call, no risk of getting a different verdict on a page refresh (Stage 9 found
that two real LLM runs of the same ambiguous community can actually disagree, so
re-investigating on every request would be actively worse, not just slower). If no case
file exists yet, this endpoint runs a real investigation via `agent/investigator.py` +
`agent/policy.py` and writes one, so every subsequent request for that same ring is fast
after the first.

### `GET /api/metrics`
Precision/recall (ring-level and account-level), the full ₹ cost-sweep curve, the
money-optimal threshold, and the baseline-vs-graph head-to-head at each review-budget
point. This endpoint imports and calls `eval/run_eval.py`'s functions directly — it does
not reimplement any of Stage 8's metric math, it just calls the same functions that
produce `eval/report.md` and returns their output as JSON instead of a markdown table.

### `GET /api/benign`
The benign look-alike clusters (family/office/hostel/couple) and whether each one was
correctly cleared at the money-optimal threshold, plus each cluster's dominant
score-contributing feature (so a UI can explain *why* it wasn't flagged, e.g. "only
pincode sharing, baseline event rate"). Same threshold `/api/metrics` reports, so the
two views describe one consistent operating point.

## 4. CORS

The Vite dev server (Part B, Stage 11) runs on `localhost:5173` by default (`4173` for a
production preview build). `api/main.py` enables CORS for both origins via FastAPI's
`CORSMiddleware`, allowing all methods/headers — there's no auth or cookies involved
in this local demo tool, so a permissive-but-scoped-to-known-dev-origins policy is
enough; it is not open to `*` (any origin).

## 5. Verification performed

Ran the server for real (`.venv/bin/uvicorn api.main:app --port 8000`) and exercised
every endpoint against the live dataset:
- `POST /api/run` → 10,209 graph nodes, 9,606 edges, 899 communities via Leiden, zero
  giant-blob flags — matches Stage 3/4's documented numbers exactly.
- `GET /api/graph` → 4,100 nodes / 4,020 edges in the size≥3-community-restricted view,
  correctly shaped `{id, type, cluster_id, suspicious, risk}` / `{source, target, type,
  weight}`.
- `GET /api/rings` → top-ranked community #11 (RING015, score 0.9115) matches Stage 9's
  demo run exactly.
- `GET /api/rings/11` → served instantly (0.04s, no LLM call) from the case file Stage 9's
  real `make demo` run had already written to disk — `is_ring: true`, confidence 0.95,
  the same card-sharing + 8.67× event-rate evidence Stage 9 documented, policy action
  `block`, plus a 31-node/30-edge evidence subgraph.
- `GET /api/metrics` → net ₹209,000, ring-level precision/recall/F1 all 1.000,
  account-level recall 85.1%, and the exact 31.7%/75.2%/90.1% vs 26.7%/46.5%/59.4%
  head-to-head numbers Stage 8 documented — confirms this endpoint is really calling
  `eval/run_eval.py`, not approximating it.
- `GET /api/benign` → 19 benign clusters, 0 false positives at threshold 0.617, matching
  Stage 8's report exactly.
- `/docs` (Swagger UI) renders and lists all six routes correctly.

## 6. What Stage 11 (the React app) needs to know

- All six endpoints are live and JSON-shaped exactly per PLAN.md §B3 — no further backend
  reshaping should be needed to wire up `react-force-graph`, the ring list table, the
  case-file panel, or the metrics charts.
- The frontend's first action on load should be `POST /api/run`, then it can freely call
  the `GET` endpoints in any order/on any schedule (they all read the same cached run
  until the user triggers another `POST /api/run`).
- Clicking into a ring that has never been investigated will be slow the first time (a
  real OpenAI call, a few seconds) and instant every time after — the UI should show a
  loading state for `GET /api/rings/{id}` rather than assuming it's always instant.
- `agent/case_files/*.json` is still gitignored — a fresh checkout has no case files
  until either `make investigate`/`make demo` or a real click into `/api/rings/{id}`
  creates one.
- Run: `make api` (starts `uvicorn api.main:app --reload` on port 8000; needs
  `make data resolve graph` to have been run at least once, and `OPENAI_API_KEY` set in
  `.env` for any never-before-investigated ring's first `/api/rings/{id}` call).
