# Progress Log — read this first if you're a new session

This file exists because this project is being built across **multiple separate
Claude Code sessions**, one (or more) per stage, with no shared memory between them.
If you're picking up this project fresh:

1. Read the **"Current state"** section below — it tells you exactly what's done,
   what's next, and anything you need to know before touching the code.
2. Read `PLAN.md` for the full design and staged build plan (§7) if you need the
   bigger picture — this file is the "what actually happened" log; `PLAN.md` is the
   "what we intended" design doc. They can drift apart; trust this file for status.
3. When you finish a stage (or make a meaningful decision/change), **append a new
   entry** at the bottom of the "Stage log" section, and update "Current state" at
   the top to reflect the new reality. Don't rewrite history — old entries stay as a
   record of what happened and why, even if a later stage changes course.

---

## Current state

**Last updated by:** Stage 13 session, 2026-09-03

**Done:**
- Stage 1 — synthetic data generation (`data/generate.py`). Run via `make data`.
  Produces `data/*.csv` (customers, orders, devices, addresses, cards, phones, ips,
  events, ground_truth). Deterministic (seeded from `config.yaml`).
- Stage 2 — entity resolution (`resolve/entity_resolution.py`). Run via
  `make resolve`. Produces `data/resolved/*.csv`. See
  [`docs/stage-2-entity-resolution.md`](docs/stage-2-entity-resolution.md) for a
  plain-language write-up of what it does and the precision testing done on it.
- Stage 3 — heterogeneous weighted graph (`graph/weights.py`, `graph/build.py`). Run
  via `make graph`. See
  [`docs/stage-3-graph-building.md`](docs/stage-3-graph-building.md) for a
  plain-language write-up, or the Stage 3 log entry below for the short version
  (graph stats and the ring-vs-family evidence contrast).
- Stage 4 — community detection (`detect/community.py`) + structure-blind baseline
  classifier (`baseline/txn_classifier.py`). Run via `make detect` (runs both). See
  [`docs/stage-4-community-and-baseline.md`](docs/stage-4-community-and-baseline.md)
  for the plain-language write-up, or the Stage 4 log entry below for the short
  version (community counts, giant-blob check, baseline ring recall/precision).
- Stage 5 — ring-likelihood scoring (`detect/features.py`, `detect/scorer.py`). Run
  via `make detect` (now runs community detection, baseline, and the scorer in
  sequence). See
  [`docs/stage-5-ring-scoring.md`](docs/stage-5-ring-scoring.md) for the
  plain-language write-up, or the Stage 5 log entry below for the short version
  (feature/weight table, ranked results, and the three fixes made while chasing
  benign-cluster false positives).
- Stage 6 — GNN anomaly layer (`detect/gnn.py`, `eval/gnn_eval.py`), optional/advanced
  tier. **Built, evaluated, and disabled by default** (`config.yaml`'s `gnn.enabled:
  false`) — the eval showed it hurts ring recall at the operating point that matters.
  See [`docs/stage-6-gnn.md`](docs/stage-6-gnn.md) for the full plain-language write-up,
  or the Stage 6 log entry below for the short version (before/after numbers, the
  determinism bug found and fixed, why it hurts rather than helps).

- Stage 7 — agent investigator (`agent/tools.py`, `agent/investigator.py`,
  `agent/policy.py`). Run via `make investigate`. See
  [`docs/stage-7-agent-investigator.md`](docs/stage-7-agent-investigator.md) for the
  plain-language write-up, or the Stage 7 log entry below for the short version (the tool
  set, the ring-vs-benign system prompt, the cost-tied action policy, and the important
  provider deviation from PLAN.md).

- Stage 8 — metrics + eval (`eval/run_eval.py`). Run via `make eval` (runs
  `eval/gnn_eval.py` then this). See
  [`docs/stage-8-eval.md`](docs/stage-8-eval.md) for the plain-language write-up, or the
  Stage 8 log entry below for the short version (headline numbers, the baseline
  head-to-head, the ₹ sweep, and the two honestly-reported recall gaps).

- Stage 9 — failure handling review + engine-level demo (`demo/run_demo.py`). Run via
  `make demo`. See
  [`docs/stage-9-failure-handling-and-demo.md`](docs/stage-9-failure-handling-and-demo.md)
  for the plain-language write-up, or the Stage 9 log entry below for the short version
  (which of PLAN.md §10's four failure modes were already handled vs. newly demonstrable,
  the `FORCE_LLM_FAILURE` env flag, and the real run's numbers).

- Stage 10 — FastAPI wrapper (`api/main.py`). Run via `make api` (uvicorn on port 8000).
  See [`docs/stage-10-fastapi-wrapper.md`](docs/stage-10-fastapi-wrapper.md) for the
  plain-language write-up, or the Stage 10 log entry below for the short version (the six
  endpoints, the in-memory run cache, the case-file-first behavior of
  `GET /api/rings/{id}`, and the live verification run's numbers).

- Stage 11 — React app: network graph + dashboard (`web/`, Vite + React + TypeScript +
  Tailwind). Run via `make web-install` once then `make web` (Vite dev server on port
  5173). See
  [`docs/stage-11-react-network-graph.md`](docs/stage-11-react-network-graph.md) for the
  plain-language write-up, or the Stage 11 log entry below for the short version (the run
  bar, `NetworkGraph.tsx`'s color/size/hover/click/focus/rings-only/legend behavior, the
  typed API client, and the live browser verification run's numbers).

- Stage 12 — case-file slide-over (`CaseFilePanel.tsx`) and metrics view (`MetricsView.tsx`),
  both built and wired into `App.tsx`. See
  [`docs/stage-12-case-file-and-metrics.md`](docs/stage-12-case-file-and-metrics.md) for the
  plain-language write-up, or the Stage 12 log entry below for the short version (the panel's
  five sections, the client.ts type fix, the evidence-subgraph sizing bug found and fixed, and
  the live-browser verification screenshots for both a flagged ring and a correctly-cleared
  benign cluster).

- Stage 13 — polish pass + README overhaul (`web/src/components/CaseFilePanel.tsx`'s new
  `Collapsible` component; screenshots under `docs/screenshots/`). See
  [`docs/stage-13-polish-and-readme.md`](docs/stage-13-polish-and-readme.md) for the
  plain-language write-up, or the Stage 13 log entry below for the short version (the audit of
  what Stage 11/12 already covered, the one real gap found and fixed, and the live-browser demo
  walkthrough used to generate the README's screenshots).

**Not started:** none — Part A (Stages 1-9) and Part B (Stages 10-13) are both complete.

**Things the next session (Stage 7, agent investigator) needs to know:**
- The graph is **bipartite** (customer nodes <-> attribute-entity nodes: device,
  card, phone, address, ip_subnet, pincode), not pre-projected to customer-customer
  edges. `graph/build.py::customer_projection()` exists if Stage 5's scoring wants a
  pairwise-weighted customer-only view instead — read the module docstring in
  `graph/build.py` for the full reasoning.
- `pincode` is **not** one of Stage 2's resolved entity types — Stage 2 only fuzzy-
  resolves full addresses. `graph/build.py` pulls the raw `pincode` column straight
  from `data/addresses.csv` itself to build the weakest-signal edge type. If you
  regenerate data with a different pincode scheme, this is the place that would
  need updating.
- Customer nodes carry `n_chargebacks`, `n_returns`, `n_cod_refusals`, `n_events`
  (aggregated from `data/events.csv`) plus `created_at`, `ring_id`, `cluster_tag` —
  `ring_id`/`cluster_tag` are ground-truth/debug labels only, not signals scoring
  should use (that would be cheating — they encode the answer).
- Edge weights come from `graph/weights.py::load_edge_weights()`, which reads
  `config.yaml`'s `edge_weights` block and **raises** if any value is missing or
  still 0.
- `data/resolved/` and `data/*.csv` are both gitignored (reproducible from
  `config.yaml`'s `random_seed` via `make data && make resolve`) — don't expect them
  to be present in a fresh checkout; regenerate them first (`make graph` depends on
  both being present but doesn't auto-run them).
- Python env: `.venv/` at repo root, created via `make setup`. Uses Python 3.14.
- `detect/community.py::detect_communities()` returns a `CommunityResult` with
  `.communities` (list of lists of customer_id, largest first), `.method`,
  `.resolution`, and `.giant_flags`. **Stage 5's `features.py`/`scorer.py` should
  import and call this directly** rather than re-running Leiden itself — it already
  handles the Leiden/Louvain fallback and the giant-blob check.
- `config.yaml`'s `community_resolution` is now `0.001`, hand-tuned to this
  synthetic dataset (see the Stage 4 log entry / doc for the sweep table). It's a
  **CPM resolution** (Leiden), which is on a very different numeric scale than the
  more familiar modularity resolution — don't be surprised that it's tiny, and
  re-sweep it if the data volume/density changes materially.
- `load_resolution()` raises if `community_resolution` is still the `0` placeholder,
  same fail-loudly policy as `graph/weights.py::load_edge_weights()`.
- The baseline classifier (`baseline/txn_classifier.py::run_baseline()`) returns a
  `BaselineResult` with `.account_scores` (customer_id -> risk score) and
  `.budget_results` (review-budget-fraction -> ring recall/precision/counts) — Stage
  8's eval/head-to-head report should pull the ring recall/precision numbers from
  here rather than recomputing them, so the "graph vs baseline" comparison is
  guaranteed to use the same methodology on both sides.
- The baseline is intentionally evaluated at a **top-N% review-budget** operating
  point (2%/5%/10%), not a fixed probability threshold — a fixed threshold on this
  model's balanced-class probabilities either flagged ~90% or ~0% of accounts
  depending on the cutoff, which isn't a meaningful number. If Stage 5's scorer
  reports its own recall/precision for the head-to-head, match this same budget
  framing (e.g. "top 5% of communities/accounts by ring-likelihood score") so the
  comparison in Stage 8 is apples-to-apples.
- `detect/scorer.py::score_communities()` returns a list of `CommunityScore`
  (`.community_index`, `.size`, `.members`, `.score` in [0,1], `.sub_scores` dict of
  the six per-feature 0-1 values, `.features` the raw `CommunityFeatures`), already
  sorted highest-score-first. **Stage 7's agent investigator should call this
  directly** (and `detect/features.py::compute_community_features()` if it needs raw,
  un-squashed numbers for its reasoning/evidence text) rather than recomputing
  features itself.
- `config.yaml`'s `ring_score_threshold` is still the `0` TODO placeholder — Stage
  5's `scorer.py` doesn't read it (it ranks everything and prints all communities
  size >= 3, doesn't threshold/filter). Whichever stage first needs an actual
  "flag this community" cutoff (Stage 7's agent, or Stage 8's eval) should set a
  real value here and decide whether to add a `load_threshold()`-style loader with
  the same fail-loudly-on-0 policy as `edge_weights`/`community_resolution`.
- `detect/scorer.py`'s six feature weights (`WEIGHTS` dict, sums to 1.0) are
  hand-picked based on which features the Stage 1 generator makes reliable vs.
  corroborating (see the module docstring and
  [`docs/stage-5-ring-scoring.md`](docs/stage-5-ring-scoring.md) §3) — not learned
  from data, since using `ring_id`/`cluster_tag` to fit weights would be the same
  cheating problem as using them as a raw feature. If Stage 8's eval wants to try
  learned weights (e.g. logistic regression on the six features, evaluated only
  out-of-sample) that's a legitimate *evaluation-time* comparison to make, just don't
  let it quietly become what `scorer.py` ships by default without flagging the
  methodology change here.
- The exit test (`scorer.py main()`'s PASS/FAIL line) currently passes with a real
  margin on this dataset/seed (worst ring 0.617 vs. best benign 0.582) — if you
  regenerate data with a different seed/volume, re-run `make detect` and check this
  line before assuming Stage 5 still separates cleanly; it's tuned to this dataset's
  behavioral gap (see the doc §5 for the three feature fixes that got it here), not
  guaranteed by a hardcoded threshold.
- `detect/community.py`'s resolution=0.001 only recovers **14 of 15** rings as
  identifiable communities (one ring's members apparently didn't cluster together
  tightly enough to form its own size>=3 community at this resolution) — Stage 5's
  ranking is only evaluated against the 14 that Stage 4 actually surfaced. If Stage 8
  wants a true 15/15 ring-level recall number, that gap traces back to Stage 4's
  resolution tuning, not Stage 5's scoring.
- `detect/gnn.py` (Stage 6) exists, is fully wired into `detect/scorer.py`
  (`score_communities(..., use_gnn=...)`, `weights_with_gnn()`), and works end-to-end,
  but is **disabled** (`config.yaml`'s `gnn.enabled: false`) because
  [`docs/stage-6-gnn.md`](docs/stage-6-gnn.md)'s eval showed it hurts ring recall at 0%
  FP rate. Stage 7's agent investigator should call `detect/scorer.py::score_communities()`
  exactly as Stage 5 left it (no `use_gnn=True` override) unless a future re-eval on a
  changed dataset flips that config flag for real. Don't resurrect the GNN path without
  re-running `eval/gnn_eval.py` first.
- If anything touching `detect/gnn.py` or `eval/gnn_eval.py` is changed, re-verify
  determinism before trusting new numbers: `torch.manual_seed()` alone is NOT enough
  (torch_geometric's `negative_sampling()` uses Python's stdlib `random` internally,
  uncovered by torch's seed) — `train_gae()` seeds `random`/`numpy`/`torch` together and
  forces `torch.use_deterministic_algorithms(True)` + single-threaded execution. See
  `docs/stage-6-gnn.md` §6 for how this was found and verified.
- `torch`/`torch-geometric` (2.14.0 / 2.8.0.post1 as installed) import and run cleanly
  on this repo's Python 3.14 venv — not a given, since PyTorch support for brand-new
  Python versions usually lags, so this was verified rather than assumed. If `make
  setup` is re-run on a different Python version, re-verify this before relying on it.
- `eval/gnn_eval.py` is a narrow, Stage-6-specific eval (ring recall vs FP rate, GNN
  on/off) that exists because Stage 8's `eval/run_eval.py` isn't built yet. Stage 8
  should decide whether to absorb/call it or supersede it with the fuller ₹-cost/
  baseline-delta harness PLAN.md describes for that stage — it's not meant to be the
  final word on eval, just enough to answer Stage 6's specific exit-test question.

**Things the next session (Stage 8, metrics + eval) needs to know:**
- **Provider deviation from PLAN.md: the agent uses OpenAI (`gpt-4o`), not Anthropic.**
  PLAN.md's tech stack says "Claude (anthropic SDK)"; this build switched to the `openai`
  package because no Anthropic key was available and the project owner asked to use an
  OpenAI key instead. `requirements.txt` has `openai`, not `anthropic`; `.env.example` has
  `OPENAI_API_KEY`, not `ANTHROPIC_API_KEY`. See
  [`docs/stage-7-agent-investigator.md`](docs/stage-7-agent-investigator.md) §6 for exactly
  what would need to change to switch back (only `agent/investigator.py`'s request/response
  plumbing and `agent/tools.py`'s `TOOL_DEFINITIONS` shape — the tool implementations and
  all of `agent/policy.py` are provider-agnostic).
- **No LLM API key was available when Stage 7 was built.** Every deterministic piece (the
  five tools in `agent/tools.py`, the policy/cost-model logic in `agent/policy.py`) was
  verified directly against real data. The tool-use loop itself was verified end-to-end
  with a scripted fake OpenAI client (fixed tool-call sequence + a hand-written verdict),
  which validates the *wiring* but is not real LLM output — those test case files were
  deleted rather than kept. **Before trusting any case file as a real example for Stage 8's
  eval, the README, or the demo, run `make investigate` for real** (needs `OPENAI_API_KEY`
  in `.env`) and confirm the output still looks sane — a fresh model call could reason
  differently than the scripted dry run did.
- `agent/investigator.py::investigate_community()` takes a `client` param specifically so
  Stage 8's eval can inject either a real `OpenAI()` client (for a real run) or a scripted
  fake one (for fast, free, deterministic tests of the surrounding pipeline) — see the
  mocked-client pattern used during Stage 7's own verification for the shape to reuse.
- `config.yaml`'s `cost_model` block is no longer all-zero placeholders — Stage 7 filled in
  `avg_order_value` (₹1,200), `chargeback_cost` (₹2,500), `false_block_cost` (₹1,500),
  `block_confidence_threshold` (0.85), `hold_confidence_threshold` (0.6). These are rough
  SMB e-commerce assumptions, not fitted to real unit economics — Stage 8's eval is a
  reasonable place to sanity-check or refine them if the ₹ sweep/threshold selection needs
  more grounded numbers. `agent/policy.py::load_cost_model()` fails loudly if any of these
  regress to `0`/missing, same policy as `graph/weights.py`/`detect/community.py`.
- `agent/policy.py::decide_action()` can only ever downgrade the agent's proposed action,
  never upgrade it — if Stage 8's eval wants to measure "how often did the policy have to
  intervene," `PolicyDecision.was_downgraded` / `.downgrade_reason` on every decision is
  the field to aggregate, no need to recompute anything.
- `agent/case_files/*.json` is gitignored (LLM output, not byte-reproducible across runs) —
  Stage 8/9's demo will need to actually run `make investigate` fresh rather than expect
  committed example case files to be present.
- Stage 7's exit test (PLAN.md: "on a real ring → correct verdict + evidence + action; on a
  benign family → agent explicitly clears it") has NOT yet been run against a real LLM
  end-to-end — the dry run above proves the pipeline is wired correctly, but the actual
  qualitative check (does gpt-4o's reasoning hold up, does it correctly rule out the benign
  explanations unprompted) still needs a real `make investigate` run and a human read of
  the resulting case files before this exit test can be marked genuinely passed.

**Things the next session (Stage 9, failure handling + demo + README) needs to know:**
- `eval/run_eval.py`'s money-optimal threshold on this dataset/seed is **0.6170** (same
  number Stage 5's own PASS/FAIL check calls out as "worst ring 0.617"), giving
  **net ₹209,000** (₹215,000 saved − ₹6,000 lost), ring-level precision/recall/F1 all
  **1.000** (14/14 labeled ring communities caught, 0 benign communities flagged),
  account-level recall **85.1%** (86/101 ring accounts flagged), and **zero** false
  positives on all 19 benign look-alike (family/office/hostel/couple) communities. The
  head-to-head vs. Stage 4's baseline: graph recall 31.7/75.2/90.1% vs. baseline
  26.7/46.5/59.4% at the 2%/5%/10% review-budget points — a clear, honestly-measured win.
  Re-run `make eval` if the data/config changes before quoting any of these in the demo
  or README.
- **Two recall gaps are reported on purpose, not hidden** — Stage 9's demo/README should
  keep surfacing both rather than only quoting the flattering top-line number:
  1. Stage 4's community detection only recovers 14 of the 15 injected rings as their
     own identifiable community (RING006 never clusters into a size>=3 group at the
     tuned resolution) — this eval's ring-level universe is those 14, not 15. The
     honest "of all 15 originally injected, how many did the full pipeline catch"
     number is 14/15 = 93.3%, lower than the 100% ring-level number quoted above.
  2. Account-level recall (85.1%) is meaningfully below ring-level recall (100%)
     because some individual accounts belonging to an otherwise-caught ring don't
     themselves land inside that ring's community (they end up in a smaller/different
     community that doesn't clear the threshold) — 15 such accounts, plus RING006's 4,
     account for all 19 account-level false negatives. See `eval/report.md` section 6
     ("what this eval doesn't cover") for the full accounting; this traces back to
     Stage 4's `community_resolution` tuning, not anything in `eval/run_eval.py`.
- `eval/run_eval.py::_account_scores()` is the one place that turns a community score
  into a per-account score (every member inherits their community's score; accounts in
  no size>=3 community score 0.0) — if Stage 9's demo wants a single "this account's
  risk score" number to show in the UI, reuse this function rather than recomputing the
  rollup.
- `eval/run_eval.py::cost_sweep()` / `money_optimal_point()` sweep **every distinct
  score value actually present in the data** as a candidate threshold (not a fixed grid)
  — if a future session wants `config.yaml`'s `ring_score_threshold` TODO placeholder
  (still `0`) filled in with a real operating value, 0.6170 is the number this eval
  computed and is a reasonable candidate, but that decision was left to Stage 9/whoever
  wires a live threshold rather than made here, since `detect/scorer.py` still ranks
  everything unfiltered by design (see Stage 5's "things next session needs to know").
- `eval/run_eval.py` calls `baseline.txn_classifier.run_baseline()` fresh each run (not
  cached) — this retrains the logistic regression from scratch (fast, a few seconds),
  so `make eval`'s numbers for the baseline side are always freshly computed against
  whatever `data/orders.csv`/`events.csv` currently contain, not stale.
- Chart color choices in `eval/run_eval.py` (`COLOR_NET`/`COLOR_SAVED`/`COLOR_LOST`/
  `COLOR_GRAPH`/`COLOR_BASELINE`) are a plain, colorblind-reasonable palette (green/red/
  blue/orange) picked for this stage's four PNGs — Stage 9's demo assets/README should
  either reuse these constants or pick a deliberately consistent palette of its own
  rather than mixing ad hoc colors across the project's various charts.
- `eval/gnn_eval.py` (Stage 6's narrower eval) and `eval/run_eval.py` (this stage) are
  BOTH still run by `make eval`, in that order — they answer different questions (GNN
  on/off decision vs. the full metrics/₹/baseline-delta report) and neither supersedes
  the other; don't merge them without re-reading both docstrings first.

**Things the next session (Part B, Stage 10 — FastAPI wrapper) needs to know:**
- `demo/run_demo.py::main()` is the reference for how to drive the whole engine end-to-end
  in the right order (build_graph -> detect_communities -> score_communities ->
  investigate_community -> decide_action -> write_case_file) — Stage 10's `POST /api/run`
  handler should follow the same call sequence rather than re-deriving it, just returning
  the results as JSON instead of printing them.
- `demo/render_evidence_subgraph()` (in `demo/run_demo.py`) and `graph/visualize.py`'s
  `_draw`/`_subgraph_for` are matplotlib-based, static-PNG renderers — fine for this
  engine-level demo, but Part B's `GET /api/rings/{id}` is specified to return
  nodes+edges as JSON (not an image) for `react-force-graph` to render interactively.
  Don't reuse the PNG renderer for the API; extract a small nodes/edges-as-JSON helper
  from `_subgraph_for`'s subgraph-selection logic instead (same node/edge selection, JSON
  output instead of a matplotlib figure).
- `FORCE_LLM_FAILURE=1` (env var, checked in `agent/investigator.py`'s `_run_tool_loop`)
  is a real, live hook for demonstrating/testing the degraded path — Stage 10's API tests
  can set this env var to exercise the `degraded` case-file path over HTTP without needing
  a real API outage.
- `agent/case_files/*.json` (gitignored, one per investigated community) already has
  everything `GET /api/rings/{id}`'s case-file payload needs (verdict, evidence,
  benign_explanations_considered, reasoning, full tool_call_trail, policy_decision) — Stage
  10 can likely serve this file's contents close to as-is rather than reshaping it.
- The real `make demo` runs (§ Stage 9 log below) confirm the OpenAI-backed agent works
  correctly end-to-end on a real ring (twice, consistently) — this was the first genuine
  (non-scripted-fake-client) LLM run in this project, closing the gap Stage 7 and Stage 8
  both flagged as still-unverified. **But it also caught a real false positive** on the
  hardest benign look-alike (community #71, ground truth `independent`) in one of the two
  runs — same prompt, same tools, same evidence, different LLM output, one run correctly
  cleared it and one incorrectly called it a ring and would have blocked it. Any web-layer
  demo/screenshots should not assume every real investigation will match a previously-seen
  "good" run; if Stage 10+ builds anything that runs a live investigation on demand (e.g.
  a "re-investigate" button), it should surface the agent's confidence/evidence plainly
  enough that a human reviewer could catch this kind of overconfident miss, and Part B's
  UI copy shouldn't imply the agent is infallible on ambiguous cases. A self-consistency
  check (re-investigate + require agreement before allowing `block`) is a reasonable
  future hardening this stage flagged but didn't implement.

**Things the next session (Part B, Stage 11 — React app) needs to know:**
- All six PLAN.md §B3 endpoints are live in `api/main.py` and JSON-shaped exactly as
  specified — `POST /api/run`, `GET /api/graph`, `GET /api/rings`, `GET /api/rings/{id}`,
  `GET /api/metrics`, `GET /api/benign`. Verified against the real dataset (see
  `docs/stage-10-fastapi-wrapper.md` §5) — no further backend reshaping should be needed
  to wire up `react-force-graph`, a ring list table, a case-file slide-over, or Recharts
  metrics views.
- The frontend's first action on load should be `POST /api/run` (returns a `run_id` and
  top-line stats); every `GET` endpoint after that reads from the same in-memory cached
  run until another `POST /api/run` is made. A `GET` before any `POST /api/run` returns
  HTTP 409, not a 500 or empty data — handle that as an explicit "click Run detection
  first" state, not a generic error.
- `GET /api/rings/{id}` is slow (a real OpenAI call, a few seconds) the first time a given
  community is opened, and instant every time after (served from the
  `agent/case_files/community_<id>.json` written on that first call). The UI needs a
  loading state for this endpoint specifically — don't assume every ring click is instant.
- `agent/case_files/*.json` is gitignored — a fresh checkout / fresh clone has zero case
  files until either `make investigate`/`make demo` is run once, or the API itself creates
  them on demand via real `/api/rings/{id}` clicks (needs `OPENAI_API_KEY` in `.env`).
- CORS is enabled for `http://localhost:5173`, `http://127.0.0.1:5173` (Vite dev server
  default), and `http://localhost:4173` (`vite preview`) — if Stage 11's dev server runs
  on a different port, add it to `api/main.py`'s `CORSMiddleware` origins list.
- Run: `make api` (starts `uvicorn api.main:app --reload` on port 8000; needs
  `make data resolve graph` to have been run at least once first).

**Things the next session (Part B, Stage 12 — case file + metrics views) needs to know:**
- `web/src/components/CaseFilePanel.tsx` and `web/src/views/MetricsView.tsx` are still
  untouched one-line stub files, not imported/rendered anywhere in `App.tsx` yet — Stage
  12 is building both from scratch, not extending existing markup.
- `web/src/api/client.ts` already has fully-typed `getRing(id)`, `getMetrics()`, and
  `getBenign()` functions matching `api/main.py`'s real response shapes field-for-field —
  call those directly, don't add new fetch logic or re-derive the types.
- `App.tsx` already owns a `selectedClusterId: number | null` piece of state, shared
  between `NetworkGraph` (click a node) and `RingList` (click a row). That's the natural
  trigger for the case-file panel: when it goes non-null, call `getRing(selectedClusterId)`
  and slide the panel in; when it goes null (background click), slide it back out. No new
  selection-state plumbing should be needed.
- `GET /api/rings/{id}` is slow the *first* time a given community is opened (a real
  OpenAI call, a few seconds) and instant after (served from
  `agent/case_files/community_<id>.json`) — the case-file panel needs its own loading
  state for this fetch, separate from the graph/ring-list's "Run detection" loading state
  which is already handled in `App.tsx`.
- The case file payload's `verdict.reasoning` is prose meant to be shown as a readable
  narrative (PLAN.md §B5: "show the agent's reasoning as a readable narrative, not raw
  JSON — this sells the 'agent'") — don't just `JSON.stringify` the case file into a
  `<pre>` block.
- `evidence_subgraph` on the case-file response is shaped identically to `GET
  /api/graph`'s payload (`{nodes, edges}`), scoped to just that community + its shared
  entities — `NetworkGraph.tsx`'s existing node/link rendering logic (color, size, hover)
  can likely be reused almost as-is for the panel's small evidence-subgraph preview,
  rather than writing a second graph renderer from scratch.
- `SummaryCards`' false-positive-rate figure in `App.tsx` is currently a client-side
  approximation computed from `/api/rings` alone (suspicious-labeled benign communities /
  all benign communities) — `GET /api/benign` already returns the precise version
  (`n_false_positives / n_benign_clusters`, plus each cluster's `top_feature` and
  `sub_scores` for an honest "why it was cleared" explanation). Stage 12's metrics view
  should use `/api/benign` directly, and `App.tsx`'s approximation is safe to simplify or
  remove once that's wired up, rather than keeping two slightly-different versions of the
  same number on screen.
- No chart library is installed yet (`web/package.json` currently has none) — PLAN.md
  names Recharts for `MetricsView.tsx`; add it (`npm install recharts` in `web/`) at the
  start of Stage 12 rather than assuming it's already available.
- `web/.env.example` documents `VITE_API_BASE_URL` (defaults to `http://localhost:8000`
  in `src/api/client.ts` if unset) — no new env var should be needed for Stage 12's calls,
  they go through the same `request()` helper.

**Things the next session (Part B, Stage 13 — polish + demo recording + README) needs to
know:**
- `client.ts`'s `RingCaseFile.verdict` type originally claimed a `recommended_action` field
  that **does not exist** in the real case-file JSON on disk (`agent/policy.py::write_case_file()`
  deliberately omits it from the persisted `verdict` block — see that function's source). Stage
  12 fixed the type to match reality and pulls the recommended action from
  `policy_decision.action` (the bounded/actual one) and `policy_decision.agent_recommended_action`
  (the agent's un-downgraded proposal) instead. If any future stage adds new fields to the case
  file JSON, re-check `client.ts` against `agent/policy.py::write_case_file()`'s actual dict
  literal rather than trusting the type file alone — this bug shipped silently in Stage 11
  because nothing exercised that field until Stage 12 actually rendered it.
- `CaseFilePanel.tsx`'s evidence-subgraph mini force-graph (`react-force-graph-2d`) renders
  **blank** if given no explicit `width`/`height` — it defaults to `window.innerWidth`/
  `innerHeight`, which draws the graph far outside the small clipped preview box. Fixed by
  giving it its own `ResizeObserver` + container ref, same pattern `NetworkGraph.tsx` already
  uses — copy that pattern again for any future small/embedded force-graph instance rather than
  assuming default sizing works inside a constrained container.
- The case file's `verdict.reasoning` prose and `evidence`/`benign_explanations_considered`
  arrays are rendered directly (no truncation) — on a long-winded model response this could run
  long; Stage 13's polish pass may want a "show more" collapse if that turns out to look bad on
  a real long case file, not addressed here since every real case file seen so far (ring #11,
  #71; benign #6) reads fine at full length.
- `MetricsView.tsx` renders all 4 charts from a single `Promise.all([getMetrics(), getBenign()])`
  — if either call fails (e.g. hits the `GET /api/rings/{id}`-style 409 before any
  `POST /api/run`), the whole view shows one shared error message rather than partial charts.
  That's deliberate (all 4 charts describe the same run) but worth knowing if Stage 13 wants
  more granular per-chart error states.
- The Network/Metrics tab toggle added to `App.tsx`'s header only appears once `phase ===
  'ready'` (i.e. after a successful `POST /api/run`) — there's no dedicated empty/idle state
  for `MetricsView` itself since the tab is unreachable before a run exists. If Stage 13 wants
  metrics reachable via a direct URL/route before a run, that'll need real routing (this app has
  none yet — tab state is a plain `useState`, not a URL param).
- Live-verified via a scripted headless-Chromium (Playwright) driver against both `make api`
  (port 8000, already-cached run) and `make web` (port 5173) running simultaneously — not just
  `tsc -b`. Playwright was installed as a one-off verification tool in the session scratchpad
  (same approach Stage 11 used), not added to `web/package.json`.

---

## Stage log

### Stage 1 — Synthetic data generation
*(Built before this log existed — reconstructed from code/PLAN.md for continuity.)*

- Implemented `data/generate.py`. Generates ~1500 legit customers (families, an
  office IP cluster, a hostel/PG pincode cluster, couples sharing a card — the
  "honesty trap" benign look-alikes — plus independent legit customers) and 15 fraud
  rings with coordinated device/card/address sharing, burst account creation, and
  elevated chargeback/return/COD-refusal rates.
- Output: `data/customers.csv`, `devices.csv`, `addresses.csv`, `cards.csv`,
  `phones.csv`, `ips.csv`, `orders.csv`, `events.csv`, `ground_truth.csv`.
- Deliberately injects messy text (typos, "Apt" vs "Apartment", extra landmark text,
  inconsistent phone formatting) so Stage 2 has real work to do.
- Run: `make data`.

### Stage 2 — Entity resolution
*Session date: 2026-09-02*

- Implemented `resolve/entity_resolution.py`. No LLM — deterministic rules only.
- Addresses: normalized text + `rapidfuzz` fuzzy matching, blocked by
  (city, pincode), gated by an extracted room/unit number so near-identical text
  with a *different* unit number (e.g. two hostel rooms) never merges. Union-find
  collapses transitive near-duplicate clusters.
- Phones: normalized to E.164 via the `phonenumbers` library.
- Devices/cards: normalized exact match (deliberately not fuzzy — see doc for why).
- IPs: bucketed into /24 subnets.
- Output: `data/resolved/resolved_{addresses,phones,devices,cards,ips}.csv`, each
  with a canonical `*_entity_id` column plus `customer_id` for joining.
- Wired `make resolve` into the Makefile.
- **Found and fixed while testing precision:**
  1. Hostel rooms ("Room 101" vs "Room 106", same building) were wrongly merging on
     text similarity alone (89-93% score) — fixed by requiring unit numbers to also
     match/be-compatible before merging.
  2. A pandas NaN-vs-None mismatch in the unit-compatibility check was silently
     blocking some legitimate merges — fixed by using `pd.isna()`.
  3. Documented (didn't "fix" — it's a caller responsibility) that reading
     `resolved_phones.csv` without `dtype=str` corrupts the `e164` column.
- **Verified against Stage 1's honesty-trap clusters:** hostel (40→40, zero false
  merges), office cluster (30→30, zero false merges), families (each correctly
  collapses to their one real shared address), fraud rings using the address-sharing
  strategy correctly collapse their near-duplicate variants.
- Full write-up: [`docs/stage-2-entity-resolution.md`](docs/stage-2-entity-resolution.md).
- Run: `make resolve` (after `make data`).

### Stage 3 — Heterogeneous weighted graph
*Session date: 2026-09-02*

Full write-up: [`docs/stage-3-graph-building.md`](docs/stage-3-graph-building.md).

- Implemented `graph/weights.py`: `load_edge_weights()` reads `config.yaml`'s
  `edge_weights` block and raises if anything is missing or still at the 0
  placeholder (fails loudly instead of silently building a meaningless flat-weight
  graph). Filled in the actual scale in `config.yaml` (was all zeros):
  device=1.0, card=0.9, phone=0.7, address=0.6, ip_subnet=0.2, pincode=0.1. Full
  justification for the ordering/gaps is in the module docstring — short version:
  device/card require deliberate reuse (near-impossible by accident) vs.
  pincode/ip_subnet which whole neighborhoods/ISP blocks share innocently.
- Implemented `graph/build.py`: builds a **bipartite** `networkx.Graph` — customer
  nodes on one side, attribute-entity nodes (device/card/phone/address/ip_subnet/
  pincode) on the other, joined via Stage 2's `*_entity_id` canonical columns (per
  the join-key gotcha the Stage 2 session flagged). Chose bipartite over an
  eagerly-projected customer-customer graph so (a) the agent investigator can later
  say *which* device/card/etc. tied accounts together, not just "they're linked",
  and (b) high-degree low-weight entity types (pincode, ip_subnet) don't blow up
  into O(k^2) projected edges. Added `customer_projection()` as an opt-in helper
  for anything downstream that wants the pairwise view instead — documented the
  tradeoff in the module docstring so Stage 4 can make an informed call.
  `pincode` isn't one of Stage 2's resolved entity types, so it's pulled straight
  from `data/addresses.csv`'s raw `pincode` column. Customer nodes carry
  `created_at`, `ring_id`, `cluster_tag` (ground truth/debug only — not for
  scoring) plus per-customer `n_chargebacks`/`n_returns`/`n_cod_refusals`/
  `n_events` aggregated from `data/events.csv`, for Stage 4's `features.py`.
- Wired `make graph` → `python -m graph.build`.
- **Verified the exit test from PLAN.md §Stage 3** by printing internal edges for
  one ring and one benign family (`graph/build.py`'s `main()` does this
  automatically — picks the lowest-numbered ring and first family cluster):
  - **Ring RING001** (7 members): 2 internal shared entities, both HIGH-weight —
    one shared `device` (w=1.0, 5/7 members) and one shared `card` (w=0.9, 5/7
    members). Total internal weight 1.9, **100%** of it from device/card.
  - **Family family_0** (3 members): 2 internal shared entities, both
    low/medium-weight — one shared `address` (w=0.6, 3/3 members) and the pincode
    that address sits in (w=0.1, 3/3 members). Total internal weight 0.7, **0%**
    from device/card.
  - This is exactly the contrast PLAN.md asks for: the ring is tied together by
    rare, deliberate shares; the family by an innocent shared residence. Full
    graph stats from the same run: 10,209 total nodes (1601 customer, 1601 phone,
    1572 ip_subnet, 1561 device, 1542 card, 1530 address, 802 pincode — pincode
    count is lower because it's a raw grouping key with real duplicates across
    unrelated customers, not a Stage-2-resolved 1:1-ish entity) and 9,606 total
    edges (1601 of each type — every customer has exactly one device/card/phone/
    address/ip/pincode row in the synthetic data, so all edge-type counts are equal
    here; that will change once Stage 4+ dedupes multi-order customers with
    multiple devices etc., which the current synthetic generator doesn't produce).
- Run: `make graph` (after `make data && make resolve`).

### Stage 4 — Community detection + baseline classifier
*Session date: 2026-09-02*

Full write-up: [`docs/stage-4-community-and-baseline.md`](docs/stage-4-community-and-baseline.md).

- Implemented `detect/community.py`: weighted community detection over Stage 3's
  bipartite graph, returning customer-only communities (entity nodes are dropped from
  the output — they've done their job of pulling related customers together).
  **Leiden** (`leidenalg` + `python-igraph`) is preferred over **Louvain**
  (`python-louvain`, kept as an auto-fallback if Leiden's libs aren't importable)
  because Leiden guarantees every returned community is internally connected, which
  Louvain doesn't. `detect_communities()` returns a `CommunityResult`
  (`.communities`, `.method`, `.resolution`, `.giant_flags`).
- **Giant-blob handling** (the "don't crash, flag it" requirement): `flag_giant_communities()`
  flags any community claiming ≥20% of all clustered customers, without raising or
  dropping it — `main()` prints flags loudly as a tuning note. Verified the flag logic
  directly against synthetic oversized input (didn't rely on the real dataset
  happening to trigger it, since our tuned resolution doesn't produce one).
- **`community_resolution` had to be tuned from scratch** — it was a `0` placeholder
  in `config.yaml` (`load_resolution()` now raises on that, mirroring
  `graph/weights.py`'s policy). Leiden's CPM resolution is on a very different scale
  than modularity resolution: `1.0` fragmented everything into 1,601 singletons.
  Swept down to **0.001**, which recovers 11/15 injected rings at ≥80% same-community
  purity, produces zero giant-blob flags, and — importantly — keeps the honesty-trap
  clusters appropriately *fragmented* rather than falsely merged: the 40-person hostel
  (pincode-only signal) splits across 39 communities, the 30-person office (IP-subnet-
  only) across 25, vs. 899 communities total (465 singletons). Full sweep table and
  reasoning in the doc.
- Implemented `baseline/txn_classifier.py`: logistic regression trained on
  **transaction-level-only** features (log amount, promo usage, account age at order
  time, order hour/day-of-week, order sequence number, days since previous order) —
  deliberately never touches the graph, a community, or any shared-entity signal.
  Target: does this order have an associated chargeback/return/cod_refusal event
  (`data/events.csv`) — a real, non-cheating label, evaluated only on a held-out test
  split (precision 0.071, recall 0.553, F1 0.125, ROC-AUC 0.608 — noisy, as expected
  for a highly imbalanced ~5%-positive target on weak features).
- **Account-level ring recall/precision** (the actual point of the exercise):
  rolled per-order scores up to a per-account risk score (max across that account's
  orders), then evaluated at a **top-N% review-budget** operating point (2%/5%/10%)
  rather than a fixed probability cutoff — a fixed 0.5 threshold flagged 89% of all
  accounts (meaningless) because of how `class_weight="balanced"` spreads out
  probabilities; a review-budget framing is both realistic (mirrors a fraud-ops
  team's finite queue) and gives a stable number. Results:
  - top 2% (32 accounts): **26.7% ring recall**, 84.4% precision
  - top 5% (80 accounts): **46.5% ring recall**, 58.8% precision
  - top 10% (160 accounts): **59.4% ring recall**, 37.5% precision
  - Note: recall isn't rock-bottom because Stage 1's rings *do* have elevated
    chargeback/return/cod_refusal rates by design (92% of ring accounts have ≥1 bad
    event vs. 32% of legit) — so the baseline isn't flying totally blind, it just has
    no way to tell "coordinated ring" apart from "one individually risky-looking
    account," which is exactly the gap Stage 5's graph-based scorer needs to close.
- Wired `make detect` → runs `detect.community` then `baseline.txn_classifier` in
  sequence (partial per the plan — Stage 5's `scorer.py` isn't built yet, so `detect`
  currently reports communities + baseline only, not ring-likelihood scores).
- Run: `make detect` (after `make data && make resolve && make graph`).

### Stage 5 — Ring-likelihood scoring
*Session date: 2026-09-02*

Full write-up: [`docs/stage-5-ring-scoring.md`](docs/stage-5-ring-scoring.md).

- Implemented `detect/features.py`: six per-community features, computed only from
  Stage 3's graph + Stage 1's raw `orders.csv`/`events.csv` — never from `ring_id`/
  `cluster_tag` (those are ground-truth/debug labels per `graph/build.py`'s docstring;
  reading them here would be reading the answer key, not detecting anything).
  - `high_weight_edge_share`: fraction of a community's *internal* shared-entity
    weight (via `graph/build.py::internal_edges()`) coming from device/card vs.
    phone/address/ip_subnet/pincode. A **share**, not a raw sum, so a large benign
    cluster with a lot of low-weight evidence doesn't out-muscle a small ring on
    volume alone.
  - `event_rate_ratio`: community's own (chargeback+return+cod_refusal)/orders rate
    divided by a dataset-wide baseline rate computed once (`compute_global_baseline`).
  - `timing_burst_score`: community's `created_at` spread compared against the
    *expected* spread of that many uniform-random draws over the full dataset window
    (not an absolute day-count threshold — corrects for group size, so small benign
    pairs aren't unfairly called "bursty" just because two random points are often
    close together).
  - `fresh_account_ratio`: fraction of members inside the community's own densest
    14-day creation window (sliding-window max) — a distinct cut from
    `timing_burst_score`, catches "most of the group arrived together" even when a
    couple of outlier members joined much earlier/later.
  - `promo_concentration`: Herfindahl concentration of promo codes actually used by
    the community, **scaled by promo-adoption rate** — a single incidental promo use
    doesn't score as "100% concentrated."
  - `size`: log1p-scaled, included per spec but explicitly minor.
- Implemented `detect/scorer.py`: squashes each raw feature into [0,1] (a saturating
  curve for the unbounded `event_rate_ratio`, clamps for the rest, a log-ceiling for
  size) and combines with fixed weights (0.35 event-rate, 0.25 high-weight-edge-share,
  0.20 timing-burst, 0.10 fresh-account, 0.07 promo-concentration, 0.03 size — sums to
  1.0). Weights are hand-picked from the Stage 1 generator's own construction (which
  signals are reliable-by-design vs. only-half-the-rings-do-this), not learned from
  ground truth — see PROGRESS.md's "things next session needs to know" for the
  reasoning on why learning them would reopen the cheating problem.
- **Exit test result: clean PASS.** All 14 ring communities Stage 4 recovers (RING006
  never forms its own size>=3 community at the tuned resolution — a Stage 4
  fragmentation gap, not a Stage 5 scoring gap) rank **1st through 14th** by score
  (0.912 down to 0.617). Every benign look-alike community — family, office, hostel,
  couple, independent — ranks 15th or lower (top benign score 0.582). Real margin
  between the worst ring and best benign, not a knife-edge threshold.
- **Found and fixed three feature-design bugs while chasing benign false positives**
  (the actual point of this stage, per the spec):
  1. `high_weight_edge_share` originally summed raw device/card weight instead of
     taking a share of total internal weight — large benign fragments with a lot of
     low-weight noise edges could out-score small rings on volume. Fixed by dividing
     by total internal weight.
  2. `timing_burst_score` originally used an absolute "span < N days" rule, which
     would call small benign pairs (e.g. couples) bursty just by chance (two random
     points in a year are often coincidentally close). Fixed by comparing against the
     *expected* spread for that group size under a uniform-random null, not a fixed
     day threshold.
  3. `promo_concentration` originally used raw Herfindahl concentration with no
     adoption scaling — one incidental promo use by one member registered as "100%
     concentrated." Fixed by multiplying by the fraction of the group's orders that
     actually used a promo at all.
- Wired `make detect` → now runs `detect.community`, then `baseline.txn_classifier`,
  then `detect.scorer`, in that order (community detection's result feeds both the
  baseline's account universe context and the scorer directly, so nothing is
  recomputed twice within one `make detect` run).
- Run: `make detect` (after `make data && make resolve && make graph`).

### Stage 6 (optional/advanced tier) — GNN anomaly layer
*Session date: 2026-09-03*

Full write-up: [`docs/stage-6-gnn.md`](docs/stage-6-gnn.md).

- Implemented `detect/gnn.py`: builds per-node features (degree, weighted degree,
  per-edge-type weight share, is_customer flag, and customer-only log-scaled
  n_events/account-age) over Stage 3's bipartite graph, then trains a **graph
  autoencoder** (2-layer GraphSAGE encoder via `torch_geometric.nn.GAE`, dot-product
  decoder, unsupervised link-reconstruction objective) -- picked over the supervised
  GraphSAGE classifier alternative PLAN.md also allows, specifically so `ring_id`/
  `cluster_tag` are never touched during training, consistent with every other stage's
  "ground truth is eval-only" rule. Per-node anomaly score = mean (1 - reconstruction
  probability) over that node's real edges; `gnn_community_scores()` aggregates to a
  per-community score (mean over customer members, min-max normalized to [0,1] like
  every other scorer.py sub-score).
- Added `torch` + `torch-geometric` to `requirements.txt`. **Verified they actually
  install and import on this repo's Python 3.14 venv** (torch 2.14.0, torch-geometric
  2.8.0.post1) before committing to the approach -- not guaranteed for a Python this new,
  and would have blown the whole timebox if it hadn't worked.
- Fused into `detect/scorer.py` as an optional 7th feature (`gnn_anomaly`):
  `score_communities(..., use_gnn=...)` and the new `weights_with_gnn(fusion_weight)`
  (scales the original six weights down proportionally by `1 - fusion_weight` so they
  keep their relative ordering, rather than re-picking seven weights by hand). Gated by
  `config.yaml`'s new `gnn:` block (`enabled`, `embedding_dim`, `hidden_dim`, `epochs`,
  `lr`, `fusion_weight`) -- missing/absent config defaults to `enabled: false` (unlike
  `edge_weights`/`community_resolution`'s fail-loudly-on-missing policy, since this is
  an optional tier, not a required pipeline step). When `enabled: false`,
  `score_communities()` skips GNN training entirely and produces byte-identical output
  to pre-Stage-6 `scorer.py` -- verified via `make detect`'s PASS line (worst ring rank
  14, best benign rank 15) being unchanged after this stage's changes.
- **Found and fixed a determinism bug while building the eval:** two back-to-back
  `train_gae()` calls in the same process, both after `torch.manual_seed(42)`, produced
  different final losses. Root cause: `torch_geometric.utils.negative_sampling()`'s
  fast path calls Python's stdlib `random.sample()` directly, which `torch.manual_seed`
  doesn't cover. Fixed by seeding `random`/`numpy`/`torch` together at the top of
  `train_gae()`, plus `torch.use_deterministic_algorithms(True)` and
  `torch.set_num_threads(1)` (multi-threaded CPU kernels have non-fixed float
  summation order that compounds over epochs). Verified fixed by diffing final_loss
  to full float precision across two in-process calls AND two separate `python -m
  detect.gnn` process invocations.
- Implemented `eval/gnn_eval.py`: the Stage 6 exit test PLAN.md asks for ("GNN improves
  ring recall at equal false-positive rate vs Stage 5 alone"), built standalone since
  Stage 8's `eval/run_eval.py` is still an unimplemented stub. Labels every community
  (size >= 3) as ring/benign/mixed/unlabeled (same >=80%-purity rule Stage 5's own
  PASS/FAIL check uses), ranks by score under both the Stage-5-only and GNN-fused
  weightings, and compares ring recall at every false-positive-rate level the
  Stage-5-only ranking actually reaches (top-N-by-budget framing, matching Stage 4/5's
  convention rather than a probability threshold).
- **Result: the GNN feature HURTS ring recall at the operating point that matters.** At
  0% false positives, Stage 5 alone already catches 14/14 rings (matches Stage 5's own
  documented PASS margin); fusing in the GNN feature at `fusion_weight=0.15` drops that
  to 12/14 (85.7%) -- a benign `independent` 3-person community's raw GNN sub-score
  (0.74) is high enough to push it to rank 13, ahead of a real ring at rank 14. It only
  recovers to 14/14 recall once the FP budget loosens to ~1.7%, i.e. past the point
  Stage 5 alone already reaches unaided. Diagnosis: the six hand-crafted features were
  each built by inspecting exactly what Stage 1's generator makes different about rings
  specifically (deliberate device/card reuse, burst creation, elevated event rate); the
  autoencoder's "anomaly" signal is a more generic "this node's neighborhood is
  statistically unusual" measure that has no way to distinguish "unusual because it's a
  ring" from "unusual for some unrelated reason" -- exactly the false-positive trap
  PLAN.md warns about, just via a new feature instead of a naive one.
- **Decision: `config.yaml`'s `gnn.enabled` left at `false`**, per PLAN.md's own cut
  rule ("if this doesn't help... disable it behind a config flag and note that in the
  README"). The fused code path is fully implemented and tested (`gnn.enabled: true`
  runs end-to-end via `python -m detect.scorer`), so flipping it back on is a one-line
  config change if a future dataset/feature change reverses this result -- but nothing
  currently justifies shipping it on.
- Wired `make gnn-eval` (runs `eval/gnn_eval.py` directly) and updated `make eval` to
  run it too (Stage 8's fuller eval harness isn't built yet, so `make eval` currently
  only runs this).
- Updated README's Overview/Metrics/"What broke"/Limitations sections, which had been
  left at Stage-0 scaffolding text despite Stages 1-5 being complete -- brought current
  through Stage 6.
- Run: `python -m detect.gnn` (standalone anomaly ranking), `make gnn-eval` or `python
  -m eval.gnn_eval` (the before/after comparison), after `make data resolve graph`.

### Stage 7 — Agent investigator (tool-use loop)
*Session date: 2026-09-03*

Full write-up: [`docs/stage-7-agent-investigator.md`](docs/stage-7-agent-investigator.md).

- Implemented `agent/tools.py`: five deterministic, JSON-serializable tools
  (`get_members`, `get_shared_entities`, `get_events`, `get_account_ages`,
  `compare_to_baseline`) that wrap Stage 3-5's existing code (`graph/build.py`'s
  `internal_edges()`, `detect/features.py`'s `compute_community_features()`/
  `compute_global_baseline()`) rather than recomputing anything. Every number returned is
  computed in Python before the LLM sees it, per the task spec ("keep the LLM out of
  arithmetic"). None of the tools ever expose `ring_id`/`cluster_tag`.
- Implemented `agent/investigator.py`: a manual (not SDK-beta-helper) tool-use loop — call
  the model, execute any requested tool calls via `agent/tools.py`, feed results back,
  repeat until the model returns its final answer instead of a tool call. The system
  prompt requires the agent to call at least `get_shared_entities`, `get_events`, and
  `compare_to_baseline` before concluding anything, and explicitly requires it to check and
  rule out (or confirm) four named benign explanations — family, office IP cluster,
  hostel/PG pincode, couple sharing a card — before it's allowed to say "ring." Output is a
  strict JSON verdict (`is_ring`, `confidence`, `evidence[]`,
  `benign_explanations_considered[]`, `reasoning`, `recommended_action`) enforced via
  structured outputs, not prompt-only formatting. Failure handling: one retry on any
  exception, then a `degraded` verdict (forced `manual_review`, evidence naming the raw
  Stage 5 features it fell back to) rather than crashing the batch.
- Implemented `agent/policy.py`: recomputes the actual bounded action
  (monitor/hold/manual_review/block) from the agent's confidence, whether it cited
  evidence, and a ₹ cost model — it can only downgrade the agent's proposed action, never
  upgrade it. `block` requires confidence ≥0.85 (config.yaml's new
  `block_confidence_threshold`), `hold` requires ≥0.6; a verdict with no cited evidence (or
  a degraded one) is hard-capped at `manual_review`; a benign verdict never justifies
  hold/block regardless of what was proposed. `write_case_file()` persists one auditable
  JSON per community under `agent/case_files/` (gitignored) with the full tool-call
  evidence trail, not just the final verdict.
- **Filled in `config.yaml`'s `cost_model` block**, which had been left at `0` TODO
  placeholders since Stage 5: `avg_order_value` (₹1,200), `chargeback_cost` (₹2,500),
  `false_block_cost` (₹1,500), plus two new keys `block_confidence_threshold` (0.85) and
  `hold_confidence_threshold` (0.6) that `agent/policy.py` needs. `load_cost_model()` fails
  loudly on missing/placeholder-0 values, matching `graph/weights.py`/`detect/community.py`'s
  existing fail-loudly convention.
- **Provider deviation from PLAN.md, made deliberately and mid-stage:** PLAN.md's tech
  stack specifies "Claude (anthropic SDK)." No Anthropic API key was available in this
  environment; the project owner was asked and chose to provide an OpenAI key instead. The
  agent was built/ported to the **OpenAI Python SDK** (`openai` package, Chat Completions
  API, `gpt-4o`) — same design throughout (manual loop, same five tools, same strict-JSON
  contract, same benign-explanation requirement, same failure handling), only the wire
  format differs (OpenAI "function" tools + `role: "tool"` messages + `response_format`
  json_schema, instead of Anthropic content blocks + `output_config.format`).
  `requirements.txt` now has `openai` instead of `anthropic`; `.env.example` has
  `OPENAI_API_KEY`. Full reasoning and exactly what would need to change to switch back:
  doc §6.
- Wired `make investigate` → `python -m agent.policy` (runs the Stage 7 exit test: finds
  one pure ring community and one benign-family community via ground truth — used only to
  *select* an illustrative pair, never fed to the agent — investigates both, decides
  actions, writes both case files).
- **Verification performed without a live LLM key:** all five tools were run against the
  real graph/communities and produce correct numbers (spot-checked against the same known
  ring/family pair Stage 3/5 used: ring community of 10 — 9/10 sharing one device,
  event_rate_ratio 4.39x baseline, timing_burst_score 0.946; benign family of 7 — only an
  address+pincode shared, event_rate_ratio 1.07x baseline, timing_burst_score 0.0). The
  full tool-use loop, degraded-fallback path, and policy/case-file pipeline were exercised
  end-to-end with a scripted fake OpenAI client (fixed tool-call sequence + a
  developer-written verdict) to validate every line of the message-passing and
  decision-downgrade logic — this is a wiring test, not a real LLM investigation, so **the
  two case files it produced were deleted** rather than kept as example output. Stage 7's
  actual PLAN.md exit test (a real model correctly investigating a real ring and correctly
  clearing a real benign family) still needs a genuine `make investigate` run once
  `OPENAI_API_KEY` is set — see "things the next session needs to know" above.
- Run: `make investigate` (after `make data resolve graph`, with `OPENAI_API_KEY` set in
  `abuse-ring-sentinel/.env`).

### Stage 8 — Metrics + baseline delta + ₹ cost eval
*Session date: 2026-09-03*

Full write-up: [`docs/stage-8-eval.md`](docs/stage-8-eval.md).

- Implemented `eval/run_eval.py`, superseding the empty stub. Calls Stage 4/5's existing
  `detect.community.detect_communities()`, `detect.scorer.score_communities()` (Stage-5-
  only, GNN stays off per Stage 6's decision), and `baseline.txn_classifier.run_baseline()`
  directly rather than recomputing anything — ground truth (`ground_truth.csv`,
  `customers.csv`'s `ring_id`/`cluster_tag`) is read only inside this eval module, never fed
  back into scoring.
- **Ring-level and account-level precision/recall/F1 with confusion matrices.** Ring-level:
  every size>=3 community labeled ring (>=80% one `ring_id`) or benign (majority look-alike
  `cluster_tag`), mixed/unlabeled explicitly excluded and the exclusion count reported.
  Account-level: every one of 1,601 accounts, scored by rolling its community's score up to
  every member (`_account_scores()` — accounts in no size>=3 community score 0, never
  flagged). Both evaluated at one shared threshold (see next point) so the confusion
  matrices and the ₹ number describe the same operating point.
- **₹ cost model + full threshold sweep** (`cost_sweep()`): reuses `config.yaml`'s
  `cost_model` block (same numbers Stage 7's `agent/policy.py` already uses, so both ₹
  stories in the project share one set of unit economics) — ₹ saved = ring accounts
  flagged × chargeback_cost, ₹ lost = legit accounts flagged × false_block_cost, net =
  saved − lost. Swept over every distinct score value actually present (not a fixed grid),
  `money_optimal_point()` picks the argmax, plotted in `eval/cost_sweep.png` with that point
  marked.
- **HEAD-TO-HEAD vs. Stage 4's baseline classifier** (`graph_recall_at_budget()`): both
  approaches compared at the same top-2%/5%/10%-of-accounts review-budget operating points
  (matching `baseline/txn_classifier.py`'s own framing rather than a probability threshold).
  Plotted in `eval/head_to_head_recall.png`.
- **Benign look-alike false-positive report**: every single family/office_cluster/
  hostel_pg/couple community (not a summary count) listed with its score and flagged/clear
  outcome at the money-optimal threshold, in both the console output and `eval/report.md`.
- **Results on this dataset/seed (money-optimal threshold 0.6170):**
  - Ring-level: precision/recall/F1 all **1.000** (14/14 labeled ring communities caught, 0
    benign communities flagged, 0 false positives).
  - Account-level: precision **0.956**, recall **0.851**, F1 **0.901** (86/101 ring
    accounts flagged, 4 legit accounts wrongly flagged out of 1,601 total).
  - ₹: **net ₹209,000** (₹215,000 saved, ₹6,000 lost).
  - Head-to-head ring recall: graph **31.7% / 75.2% / 90.1%** vs. baseline
    **26.7% / 46.5% / 59.4%** at 2%/5%/10% review budget — a clear, consistent win at
    every budget tested.
  - Benign look-alikes: **0/19** flagged at the money-optimal threshold (highest-scoring
    benign community, a 3-person hostel fragment, scored 0.524 vs. the 0.617 cutoff).
- **Two recall gaps reported honestly, not tuned away** (both trace back to Stage 4's
  community detection, not this eval script):
  1. RING006 never forms its own size>=3 community at the tuned `community_resolution`,
     so it's structurally absent from the ring-level universe — true full-pipeline recall
     over all 15 originally-injected rings is **14/15 = 93.3%**, lower than the 100%
     ring-level number above, and the report states both numbers side by side.
  2. Account-level recall (85.1%) is below ring-level recall (100%) because some
     individual members of otherwise-caught rings land in a different/smaller community
     that doesn't itself clear the threshold — 15 accounts (plus RING006's 4) account for
     all 19 account-level false negatives, itemized by ring_id in `eval/report.md`.
- Wired `make eval` → now runs `eval/gnn_eval.py` (unchanged, Stage 6's narrower
  GNN-specific comparison) then `python -m eval.run_eval` (this stage's full report),
  replacing the old "(Stage 8 full eval/run_eval.py not implemented yet)" placeholder line.
- Run: `make eval` (after `make data resolve graph detect`), or `python -m eval.run_eval`
  standalone. Writes `eval/cost_sweep.png`, `eval/head_to_head_recall.png`,
  `eval/confusion_ring_level.png`, `eval/confusion_account_level.png`, and
  `eval/report.md`.

### Stage 9 — Failure handling + engine-level demo
*Session date: 2026-09-03*

Full write-up: [`docs/stage-9-failure-handling-and-demo.md`](docs/stage-9-failure-handling-and-demo.md).

- **Audited PLAN.md §10's four failure-handling requirements against the existing
  codebase** rather than assuming a fresh implementation was needed. Three were already
  correctly handled as a side effect of earlier stages' own design, confirmed and
  documented here rather than rebuilt:
  1. Bad/missing attribute rows — already isolated to singleton entities by
     `resolve/entity_resolution.py`'s `resolve_phones()`/`resolve_ips()` (Stage 2).
  2. Agent over-reach guard (no `block` without cost-tied confidence) — already enforced
     by `agent/policy.py::decide_action()`'s downgrade-only cascade (Stage 7).
  3. Giant community cap + flag — already handled by
     `detect/community.py::flag_giant_communities()` (Stage 4); zero flags on this
     dataset's tuned config, as documented back in Stage 4.
- **Built the one failure mode that couldn't be demonstrated just by running the pipeline
  normally: an LLM error.** Added a `FORCE_LLM_FAILURE=1` env-var check at the top of
  `agent/investigator.py::_run_tool_loop`'s per-iteration loop — when set, it raises
  immediately before any real OpenAI request goes out, which drives the *existing*
  retry-once-then-`degraded` logic for real (including the real one-second retry sleep)
  rather than needing a real outage or a scripted fake client to exercise it. Unset, the
  normal path is completely unchanged.
- Implemented `demo/run_demo.py` (previously an empty one-line stub): runs the full engine
  (build graph -> detect communities -> score -> investigate top-ranked suspect and
  top-ranked benign look-alike with the real agent+policy -> force one degraded
  investigation -> render an evidence subgraph PNG), printing a readable summary at each
  step. Deliberately selects which community to investigate via the scorer's own #1 rank
  (not a ground-truth lookup, unlike `agent/investigator.py`'s own exit-test `main()`) so
  the demo proves the pipeline finds its own top suspect the way it would on real
  unlabeled data; picks the benign look-alike as the highest-scoring non-ring community
  specifically to showcase the closest near-miss getting correctly talked down.
- Added `render_evidence_subgraph()` to `demo/run_demo.py`, reusing `graph/visualize.py`'s
  existing `_draw`/`_subgraph_for` drawing helpers (same colors/legend/layout as Stage 3's
  ad-hoc sanity check) rather than inventing new plotting code — renders the top-ranked
  ring next to the top-ranked benign look-alike, side by side, to
  `demo/evidence_subgraph.png`.
- Wired `make demo` -> `python -m demo.run_demo` (was `@echo "not implemented"`).
- **Ran `make demo` for real against a live `OPENAI_API_KEY`, twice** (the second run was
  to verify the case-file-overwrite fix below) — the first genuine (non-scripted-fake-
  client) LLM runs in this project, closing the verification gap Stage 7 and Stage 8 had
  both explicitly flagged as still-open. Results, reported honestly rather than only the
  flattering run (both runs are in `docs/stage-9-failure-handling-and-demo.md` §4):
  - Scorer's own #1-ranked community (#11, 5 members, score 0.912) was, per ground truth
    (checked only for reporting), a real ring (RING015). The agent — never shown that
    label — independently investigated it and correctly verdicted `is_ring: true` at
    confidence 0.95 **in both runs**, citing all 5 members sharing one card (weight 0.9),
    an 8.67x-baseline event rate, a 0.99 timing-burst score, and a 1.0 fresh-account
    ratio. Policy action: `block` (evidence cited, confidence clears the 0.85 threshold,
    no downgrade) both times.
  - Highest-scoring genuinely benign community (#71, 3 members, score 0.582, ground truth
    `cluster_tag=independent` — the closest a benign cluster got to ring-like) got
    **different verdicts across the two runs**: Run 1 correctly cleared it (`is_ring:
    false`, confidence 0.60, citing only mild 1.76x-baseline event-rate elevation and no
    creation burst) with policy action `manual_review`. Run 2 **incorrectly** called it a
    ring (`is_ring: true`, confidence 0.85 — just clearing `block_confidence_threshold`),
    over-weighting a card shared by only 2 of its 3 members and the same 1.76x event rate
    it had called "mild" in Run 1, resulting in policy action `block` — a real false
    positive on a benign community that the policy layer's confidence gate did not catch
    (it enforced its own rule correctly; the rule can't fix an overconfident agent). This
    is the honest finding of this stage's real-LLM verification: the agent is reliable on
    a clear-cut case (unanimous strong evidence, tested twice) but not perfectly reliable
    on a genuinely ambiguous one (partial evidence, 3 members) — flagged in the doc as a
    reason a production system would want a self-consistency check (e.g. re-investigate
    ambiguous-scoring communities and require agreement) before trusting a single LLM
    call to authorize `block`, not implemented here as it's outside Stage 9's scope.
  - `FORCE_LLM_FAILURE=1` against the same suspect community produced two real simulated
    failures, the real retry path, then a `degraded=True` verdict hard-capped at
    `manual_review` by policy — no crash, no silent skip, a normal case file written.
  - `demo/evidence_subgraph.png` saved successfully: visibly dense, thick, red/orange
    (device/card) edges on the ring side vs. a sparser web of thin blue/purple/grey
    (address/phone/pincode, plus one partial card share) edges on the benign side.
- **Found and fixed a minor false-alarm bug while building the demo:** `run_demo.py`
  originally checked `os.environ.get("OPENAI_API_KEY")` before `agent/investigator.py`'s
  own `_client()` had a chance to call `load_dotenv()`, so it printed a spurious "API key
  not set" warning even when a real key was present in `.env` and the investigations
  below it succeeded anyway. Fixed by calling `load_dotenv()` at the top of
  `demo/run_demo.py::main()` itself, before the check.
- Added `docs/stage-9-failure-handling-and-demo.md`: the plain-language write-up covering
  all four failure modes (which were pre-existing vs. newly built), what `demo/run_demo.py`
  does step by step, the real run's numbers, and why this doesn't replace `make eval`.
- **Found and fixed a second bug during the same run:** the real investigation and the
  forced-failure demo both target the same top-ranked community (deliberately, so the
  contrast is easy to follow), but `write_case_file()` names files only by community id —
  the forced-failure run was silently overwriting the real verdict's case file. Fixed by
  writing the forced-failure demo's case file to a separate `agent/case_files/
  demo_forced_failure/` subdirectory instead of `CASE_FILE_DIR` directly, so both the real
  verdict and the degraded one are preserved on disk after one `make demo` run.
- Run: `make demo` (needs `OPENAI_API_KEY` in `.env`; requires `make data resolve graph` to
  have been run at least once). Produces `demo/evidence_subgraph.png`, the real case files
  under `agent/case_files/`, and the forced-failure demo's case file under
  `agent/case_files/demo_forced_failure/`.

### Stage 10 — FastAPI wrapper (Part B begins)
*Session date: 2026-09-03*

Full write-up: [`docs/stage-10-fastapi-wrapper.md`](docs/stage-10-fastapi-wrapper.md).

- Implemented `api/main.py`, previously a one-line stub docstring. Wraps the Part-A engine
  (Stages 3-9) as the six endpoints PLAN.md §B3 specifies, with **no detection logic in the
  API itself** — every handler calls existing engine code (`graph.build.build_graph()`,
  `detect.community.detect_communities()`, `detect.scorer.score_communities()`,
  `agent.investigator.investigate_community()`, `agent.policy.decide_action()`,
  `eval.run_eval`'s metric functions) and only reshapes the result into JSON, mirroring the
  same call sequence `demo/run_demo.py` already uses.
- **In-memory run cache**: `POST /api/run` builds the graph, detects communities, and scores
  them once, stashing the result in a single module-level `RunState` (`_RUN`). Every `GET`
  endpoint reads from this same cached object so `/api/graph`, `/api/rings`,
  `/api/rings/{id}`, `/api/metrics`, and `/api/benign` all describe one consistent run
  rather than each silently recomputing its own. A `GET` before any `POST /api/run` returns
  HTTP 409, not a crash or empty payload.
- **`GET /api/graph`**: nodes/edges shaped for `react-force-graph`
  (`{id, type, cluster_id, suspicious, risk}` / `{source, target, type, weight}`), restricted
  to customers in a size>=3 community plus their directly-shared entities — the full
  ~10,209-node bipartite graph would be too dense to render usefully, and nodes touched only
  by community-less customers aren't interesting to look at anyway. Reuses
  `graph/visualize.py`'s `_subgraph_for()` node-selection helper as-is; only the JSON shaping
  (`_graph_payload()`) is new.
- **`GET /api/rings`**: ranked list (`id, size, score, rupee_risk, status`).
  `rupee_risk` reuses `agent/policy.py`'s own per-member convention
  (`size * chargeback_cost`) so it matches what a case file reports for the same community.
  `status` (suspicious/cleared/unlabeled) is a ground-truth-derived **display label only**,
  same as every existing eval/demo script's diagnostic printing — never fed back into the
  score.
- **`GET /api/rings/{id}`**: the case-file payload. **Serves an existing
  `agent/case_files/community_<id>.json` from disk if one exists** (from an earlier
  `make investigate`/`make demo` run, or an earlier hit on this same endpoint); otherwise runs
  a real investigation via `agent.investigator.investigate_community()` +
  `agent.policy.decide_action()` and writes one. This was a deliberate design choice, not
  just an optimization — Stage 9 documented that two real LLM runs of the same ambiguous
  community can disagree, so re-investigating on every page load would make the case file
  *less* stable across a refresh, not just slower/costlier. Also returns a JSON evidence
  subgraph (same shape as `/api/graph`, scoped to just that community) via the same
  `_graph_payload()` helper.
- **`GET /api/metrics`** and **`GET /api/benign`**: both import and call `eval/run_eval.py`'s
  functions directly (`cost_sweep`, `money_optimal_point`, `ring_level_metrics`,
  `account_level_metrics`, `graph_recall_at_budget`, `_label_communities`, etc.) rather than
  reimplementing any of Stage 8's metric math — these endpoints are guaranteed to report the
  same numbers `make eval`'s `eval/report.md` does.
- **CORS** enabled via `fastapi.middleware.cors.CORSMiddleware` for
  `http://localhost:5173` / `127.0.0.1:5173` (Vite dev server default) and
  `http://localhost:4173` (`vite preview`) — all methods/headers allowed for those origins
  only (not `*`), since this is a local demo tool with no auth to protect anyway.
- Wired `make api` → `uvicorn api.main:app --reload --port 8000` (was `@echo "not
  implemented"`).
- **Verified all six endpoints against the real, already-generated dataset** by starting the
  server and hitting every route:
  - `POST /api/run`: 10,209 graph nodes, 9,606 edges, 899 communities (Leiden), zero
    giant-blob flags — matches Stage 3/4's documented numbers exactly.
  - `GET /api/graph`: 4,100 nodes / 4,020 edges in the restricted view, correctly shaped.
  - `GET /api/rings`: top-ranked community #11 (RING015, score 0.9115) — matches Stage 9's
    real demo run's top pick exactly.
  - `GET /api/rings/11`: served in 0.04s (no LLM call) from the case file Stage 9's real
    `make demo` run had already written — `is_ring: true`, confidence 0.95, the same
    card-sharing + 8.67x-baseline-event-rate evidence and `block` policy action Stage 9
    documented, plus a 31-node/30-edge evidence subgraph.
  - `GET /api/metrics`: net ₹209,000, ring-level P/R/F1 all 1.000, account-level recall
    85.1%, and the exact 31.7%/75.2%/90.1% vs 26.7%/46.5%/59.4% head-to-head numbers Stage 8
    documented — confirms this endpoint genuinely calls `eval/run_eval.py` rather than
    approximating it.
  - `GET /api/benign`: 19 benign clusters, 0 false positives at threshold 0.617 — matches
    Stage 8's report exactly.
  - `/docs` (Swagger UI) renders and lists all six routes.
- Run: `make api` (needs `make data resolve graph` to have been run at least once;
  `OPENAI_API_KEY` in `.env` needed only for a never-before-investigated ring's first
  `GET /api/rings/{id}` call — already-investigated communities from earlier
  `make investigate`/`make demo` runs serve instantly with no key needed).

### Stage 11 — React app: network graph + dashboard
*Session date: 2026-09-03*

Full write-up: [`docs/stage-11-react-network-graph.md`](docs/stage-11-react-network-graph.md).

- Scaffolded `web/` as a real Vite + React + TypeScript + Tailwind app (hand-written
  config — `npm create vite` and other package-executing installers were blocked by this
  session's sandbox classifier, so `package.json`/`vite.config.ts`/`tsconfig.json`/
  `tailwind.config.js`/`postcss.config.js`/`index.html` were all written directly rather
  than generated). The pre-existing `web/src/{api,components,views}/*` files from Stage
  10's planning were one-line stub comments, not real scaffolding — replaced with working
  implementations for everything in this stage's scope; `CaseFilePanel.tsx` and
  `MetricsView.tsx` (Stage 12 scope per PLAN.md §B6) were deliberately left as stubs.
- **`src/api/client.ts`**: a typed fetch wrapper, one function per `api/main.py` endpoint
  (`runDetection`, `getGraph`, `getRings`, `getRing`, `getMetrics`, `getBenign`), with
  interfaces hand-matched field-for-field against the real Stage 10 FastAPI response
  shapes (read directly from `api/main.py`, not guessed/re-derived). Non-2xx responses
  throw a typed `ApiError` carrying FastAPI's `detail` message, so e.g. the 409 "no run
  yet" case surfaces as a real catchable error rather than a generic fetch failure.
- **`App.tsx`** (run bar + layout): "Run detection" button drives `POST /api/run` then
  `GET /api/graph` + `GET /api/rings` in parallel, with distinct
  running/loading-results/ready/error phases surfaced in the button label and an error
  banner. Owns `selectedClusterId` as shared state between the graph and the ring list —
  clicking either drives the same focus behavior.
- **`SummaryCards.tsx`**: #rings/#accounts-flagged/₹-at-risk (red) and benign
  false-positive rate (green), computed client-side from `GET /api/rings`'s `status`/
  `ground_truth` fields — no new backend endpoint needed for this stage. Noted in the doc
  (and in the Stage 12 handoff below) that this FP-rate is an approximation; `GET
  /api/benign` (already live since Stage 10) has the precise per-cluster version Stage 12
  should switch to.
- **`NetworkGraph.tsx`** (the hero screen), built on `react-force-graph-2d`: red ring
  nodes / green benign nodes / grey shared-entity nodes, node size by risk score, a hover
  tooltip (id, type, risk, cluster, ground-truth hint), a "rings only" filter checkbox
  computed client-side from the same `/api/graph` payload, and a bottom-left legend.
  Click-to-focus dims every node/edge outside the selected cluster to near-transparent
  and zooms the camera to fit just that cluster **plus the shared-entity nodes it's
  directly connected to** (a `selectedClusterEntityIds` memo, added after the first
  verification pass looked too zoomed-out/small — entity nodes carry `cluster_id: null`
  themselves so the naive `zoomToFit` node-filter excluded them at first, cropping out the
  very shared-device/card edges that are the point of focusing a ring). Settling
  animation via `cooldownTicks={100}` + a delayed `zoomToFit` call, per PLAN.md §B5's
  "network forming" ask.
- **`RingList.tsx`**: ranked table (score descending) with color-coded status chips,
  ground-truth shown as a row title-attribute tooltip. Clicking a row sets the same
  `selectedClusterId` `NetworkGraph` reads, so table clicks and graph-node clicks stay in
  sync.
- **Design**: dark theme (`neutral-950`/`neutral-900`), two accent colors only (`danger`
  red / `safe` green, defined once in `tailwind.config.js`), generous card/table padding —
  per PLAN.md §B5.
- Wired `make web-install` (→ `cd web && npm install`) and `make web` (→ `cd web && npm
  run dev`), replacing the `@echo "not implemented"` placeholder.
- **Verified live in a real browser**, not just `tsc`/build-checked: ran
  `.venv/bin/uvicorn api.main:app --port 8000` and `npm run dev` simultaneously, then
  drove headless Chromium (via Playwright, installed only as a one-off verification tool
  for this session — not added to `web/package.json`) against `localhost:5173`. Clicking
  **Run detection** produced real numbers (21 rings found, 111 accounts flagged, ₹2,77,500
  at risk, 0.0% benign false-positive rate) and a settled network graph with visible red
  ring nodes; clicking the top-ranked row (community #11, RING015, score 0.911) dimmed the
  rest of the graph and zoomed to show exactly its 5 red member nodes radiating from one
  shared entity node. Zero browser console errors throughout. This is PLAN.md §B6 Stage
  11's exit test verified for real: "press Run → graph draws, rings are red, clicking a
  ring focuses it."
- `npx tsc -b` passes clean (strict mode, `noUnusedLocals`/`noUnusedParameters` on).
- Run: `make web-install` once, then `make web` (Vite dev server on port 5173; needs
  `make api` running on port 8000 for real data — see Stage 10's CORS config, already set
  up for this exact port).

### Stage 12 — Case-file slide-over + metrics view
*Session date: 2026-09-03*

Full write-up: [`docs/stage-12-case-file-and-metrics.md`](docs/stage-12-case-file-and-metrics.md).

- Installed `recharts` into `web/package.json` (no chart library had been added yet).
- Implemented `web/src/components/CaseFilePanel.tsx`: a right-side slide-over that opens
  whenever `App.tsx`'s existing `selectedClusterId` state goes non-null (no new selection
  plumbing needed, reusing exactly what Stage 11 left ready). Fetches `GET /api/rings/{id}`
  on open with its own loading spinner (separate from the graph/ring-list's "Run detection"
  loading state, since this fetch can be a multi-second live LLM call the first time a
  community is opened). Renders, top to bottom: a verdict banner (red "FLAGGED" / green
  "CLEARED", confidence %), the recommended bounded action (with the agent's original
  proposal shown alongside if the policy downgraded it), a small evidence-subgraph
  force-graph, shared-entity chips (device/card visually distinguished from
  phone/address/ip_subnet/pincode, matching the strong/weak evidence split
  `detect/scorer.py` already uses), four behavioral-evidence stat tiles (event-rate ratio,
  account-creation burst score, high-weight-entity share, fresh-account ratio, plus a
  chargeback/return/COD-refusal count) pulled from the case file's own
  `compare_to_baseline`/`get_events` tool-call results rather than re-deriving anything, and
  finally the agent's `reasoning` as a readable paragraph followed by its cited evidence and
  benign-explanations-considered bullet lists — never a raw JSON dump, per PLAN.md §B5.
- Implemented `web/src/views/MetricsView.tsx`: fetches `GET /api/metrics` and
  `GET /api/benign` in parallel, renders four Recharts cards in a 2x2 grid — (1) ring-level
  vs. account-level precision/recall bars, (2) the ₹ cost-sweep line chart (net/saved/lost
  across every threshold) with a `ReferenceDot` marking the money-optimal point, (3) the
  graph-vs-baseline ring-recall bar chart at the 2%/5%/10% review-budget points (the
  "punchline" per the task spec), (4) a horizontal bar chart of every benign look-alike
  cluster's score, colored red if it was a false positive at the money-optimal threshold and
  green if correctly cleared. Reuses the same dark-theme colors already established
  (`danger`/`safe` from `tailwind.config.js`) plus one amber accent for the baseline series,
  consistent with `eval/run_eval.py`'s existing chart palette rather than inventing new
  colors.
- Wired both into `App.tsx`: added a Network/Metrics tab toggle in the header (visible once
  a run is `ready`), rendering `NetworkGraph`+`RingList` or `MetricsView` depending on the
  active tab, and mounted `<CaseFilePanel clusterId={selectedClusterId} onClose={...} />`
  once at the bottom of the component tree (it's a fixed-position overlay, works from either
  tab).
- **Found and fixed a real type/data mismatch while building the panel:** `client.ts`'s
  `RingCaseFile.verdict` type (written in Stage 11, before Stage 12 ever fetched a real case
  file and looked at the actual JSON) claimed a `recommended_action` string field that
  **does not exist** on disk — `agent/policy.py::write_case_file()` deliberately excludes it
  from the persisted `verdict` block (only `is_ring`/`confidence`/`evidence`/
  `benign_explanations_considered`/`reasoning` are written; the recommended action lives
  entirely in the separate `policy_decision` object, since the agent's raw proposal is
  advisory-only per `agent/investigator.py`'s own docstring). Fixed the type to match the
  real shape and pulled the panel's "Recommended action" section from
  `policy_decision.action` (bounded/actual) and `.agent_recommended_action` (the agent's
  pre-downgrade proposal, shown only when `was_downgraded` is true).
- **Found and fixed a real rendering bug via live-browser verification:** the evidence
  subgraph's `ForceGraph2D` rendered completely blank on the first pass — it defaults to
  `window.innerWidth`/`innerHeight` when no `width`/`height` prop is given, so inside the
  panel's small clipped preview box the simulation settled far outside the visible area.
  Fixed by giving it its own `ResizeObserver`-driven container ref, mirroring the exact
  pattern `NetworkGraph.tsx` already established in Stage 11.
- **Verified live in a real browser**, not just `tsc -b`: installed Playwright as a one-off
  scratchpad tool (same approach Stage 11 used) and drove headless Chromium against
  `make api` (port 8000, already-cached run) + `make web` (port 5173) running together.
  - Clicked ring community **#11** (RING015, score 0.9115, 5 members): panel showed a red
    "FLAGGED — likely fraud ring" banner at 95% confidence, a "Block" action, a populated
    red-node evidence subgraph, a "Card · 5 members · w=0.90" chip, and alarming-red stat
    tiles (event rate 8.67x baseline, 100% high-weight-entity share, 100% fresh-account
    ratio) — matches Stage 9's documented real-LLM run on this exact community.
  - Clicked benign community **#6** (ground truth `benign:family`, 6/7 members, score
    0.1931): panel showed a green "CLEARED — benign" banner at 80% confidence, a "Monitor"
    action, a green-node evidence subgraph, neutral (not red) "Address"/"Pincode" chips, calm
    white-text stat tiles (event rate only 1.07x baseline, 0% high-weight share), and a full
    readable reasoning paragraph citing the address/pincode-only sharing and lack of any
    device/card/timing-burst signal — a real, correctly-cleared benign case file, not a
    synthetic example.
  - Switched to the **Metrics** tab: all four charts rendered with real numbers — precision/
    recall bars (matching Stage 8/10's documented ~1.000 ring-level, ~0.85 account-level
    recall), the ₹ sweep line chart with "money-optimal" labeled at threshold 0.617 / net
    ₹2,09,000 (small rounding vs. Stage 8's ₹209,000 headline, same underlying number), the
    graph-vs-baseline recall bars at 2%/5%/10% budgets, and all 19 benign clusters listed
    with **0 shown in red** (0 false positives at this threshold) — matches Stage 8/10's
    documented eval exactly.
  - Zero browser console errors across every screenshot taken.
  - This is PLAN.md's "Show me" bar for this stage, verified for real: clicking a ring opens
    a case file with real agent output (both a flagged ring and a correctly-cleared benign
    cluster), and the metrics view renders all charts.
- `npx tsc -b` passes clean (strict mode).
- Run: `make api` (port 8000) + `make web` (port 5173) simultaneously, same as Stage 11 —
  no new run commands added this stage.

### Stage 13 — Polish + demo verification + README overhaul
*Session date: 2026-09-03*

Full write-up: [`docs/stage-13-polish-and-readme.md`](docs/stage-13-polish-and-readme.md).

- **Audited PLAN.md §B6 Stage 13's checklist against what Stages 11/12 already built**, rather
  than assuming a fresh polish pass was needed. Most of it was already done as a side effect of
  earlier stages' own design:
  - Loading states: `App.tsx`'s run-bar phases (`running`/`loading-results`/`error`),
    `CaseFilePanel.tsx`'s own spinner (separate from the run-bar's), `MetricsView.tsx`'s own
    spinner — all already existed (Stage 11/12).
  - Empty states: "Press 'Run detection' to draw the network" (network view),
    "No communities yet — run detection first" (`RingList.tsx`), "No metrics available — run
    detection first" (`MetricsView.tsx`) — all already existed.
  - Error states: a dismissable error banner in `App.tsx` for a failed run, a red inline error
    in `CaseFilePanel.tsx` for a failed case-file fetch, a shared error message in
    `MetricsView.tsx` — all already existed.
  - The graph-settling animation (`cooldownTicks={100}` + delayed `zoomToFit`) and dim-on-select
    (both `NetworkGraph.tsx` and the case file's evidence-subgraph dim/zoom to the focused
    cluster) — both already existed (Stage 11).
- **The one real, previously-flagged gap: long agent reasoning text had no collapse.** Stage 12's
  own handoff notes named this directly ("a 'show more' collapse if that turns out to look bad on
  a real long case file... not addressed here"). Added a `Collapsible` component to
  `CaseFilePanel.tsx`: reasoning text over 420 characters truncates to a preview with a
  "Show more"/"Show less" toggle; short reasoning (every real case file seen through Stage 12)
  renders unchanged. `npx tsc -b` still passes clean (strict mode) after the change.
- **Verified the full PLAN.md §B7 demo flow live**, end to end, in a real headless-Chromium
  browser (Playwright, installed as a one-off scratchpad tool, same pattern Stages 11/12 used —
  not added to `web/package.json`) against the already-running `make api` (port 8000, a cached
  run from an earlier session) and `make web` (port 5173): land on the empty dashboard -> click
  Run detection -> network graph settles and rings glow red -> click the top-ranked ring
  (community #11, RING015, score 0.9115) -> graph dims/zooms to it, case file opens with a real
  95%-confidence `block` verdict -> click a benign family (community #6, `benign:family`, 6/7
  members) -> case file shows an 80%-confidence `Monitor` verdict, correctly cleared -> Metrics
  tab renders all 4 charts with real numbers matching `eval/report.md` exactly. Zero browser
  console errors across every step. Screenshots from this run saved to `docs/screenshots/` and
  embedded in the new README demo-flow walkthrough.
- **Found (not a bug — a previously-documented finding, re-encountered live) while picking a
  benign example to screenshot:** the first "cleared" row the driver picked, community #71,
  turned out to hold a *cached* case file from an earlier session where the real LLM had
  incorrectly flagged it (`is_ring: true`, confidence 0.85) — exactly the Stage 9 finding
  ("the hardest benign look-alike got different verdicts across two real runs") preserved on
  disk. Checking all six cached case files under `agent/case_files/` turned up a second one
  (community #24) with the same kind of stale over-eager verdict. Rather than deleting or
  re-investigating these (which would silently erase real evidence of the documented
  reliability gap), left them as-is and picked community #6 — confirmed via `GET /api/rings`
  to genuinely be `benign:family (6/7)` and confirmed via its case file to have `is_ring:
  false` — as the demo's stable "correctly cleared" example. Documented this explicitly in the
  new README's demo-flow section so a future screen recording doesn't accidentally pick a
  flagged row expecting it to be clear.
- Updated **README.md** end to end per this stage's brief:
  - **Overview**: rewritten from PLAN.md §0's pitch, now describing the finished system
    (engine + web dashboard) rather than the Part-A-only framing the previous README had.
  - **How to run**: split into explicit numbered steps covering both `make api` (backend,
    terminal 1) and `make web` (frontend, terminal 2), plus one-time setup and data-generation
    steps — the previous README already listed both commands but didn't explain the two-terminal
    requirement or the ordering.
  - **Demo flow**: new section, a screenshot-illustrated step-by-step walkthrough matching
    PLAN.md §B7 exactly, usable directly as a screen-recording script. Includes the honest
    caveat about community #71's flakiness so a recording doesn't stumble into it unexplained.
  - **Architecture**: replaced the `TODO: paste/adapt the ASCII diagrams` placeholder with both
    real diagrams (PLAN.md §4 engine + §B1 web), plus a short paragraph pointing at the Stage
    10/11/12 docs for exact endpoint/payload/component details.
  - **Metrics**: kept Stage 8's numbers (unchanged — no re-run of `make eval` this stage since
    nothing scoring-related changed), added a note that the same numbers render live in the
    Metrics tab, and cross-referenced the demo-flow caveat from the agent-reliability bullet.
  - **What broke and what I did**: kept all prior entries (Stage 6 GNN determinism, Stage 9
    agent false-positive and case-file-overwrite bugs) and added the two Stage 12 web bugs
    (the `recommended_action` type/data mismatch, the blank evidence-subgraph sizing bug) that
    the previous README had only in PROGRESS.md, not surfaced here.
  - **Failure handling**: kept the four PLAN.md §10 items, added two web-layer ones (the
    `GET` before `POST /api/run` 409 empty-state, the case-file panel's own loading state for
    slow first-open LLM calls) since Part B is now in scope for this section.
  - **Limitations**: reorganized from a checklist into prose, folded in the agent-reliability
    caveat and the GNN decision (previously separate checklist items), removed the stale
    "Part B not yet built" line since Part B is now done.
- Did not change any Python engine code, `config.yaml`, or re-run `make eval`/`make demo` this
  stage — nothing in Stage 13's scope touches scoring, the agent, or the cost model, so the
  Stage 8 numbers already in `eval/report.md` are still current and accurate.
- Run: `make api` (port 8000) + `make web` (port 5173) simultaneously, same as Stages 11/12 —
  no new run commands added this stage. Screenshots regenerate via the same Playwright driver
  pattern if the UI changes again (see `docs/stage-13-polish-and-readme.md` for the script).

<!--
  Next session: copy the pattern above — add a new "### Stage N — <name>" section
  here with what you built, key decisions, anything you found/fixed, and how to run
  it. Then update "Current state" above to move your stage from "Not started" to
  "Done" and add a fresh "Things the next session needs to know" list.
-->
