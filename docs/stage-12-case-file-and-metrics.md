# Stage 12 — Case-File Slide-Over + Metrics View (in plain language)

This document explains what Stage 12 added: the two screens Stage 11 deliberately left as
stubs — a **case-file slide-over** that opens when you click a ring (or a cleared benign
community) and reads like an investigator's report, and a **metrics view** with four
charts that make the project's actual results (precision/recall, ₹ savings, the
graph-vs-baseline comparison, and the honestly-reported benign false positives) visible
without reading a terminal report.

Same rule as every earlier web/API stage: **no detection logic lives here.** Both screens
only render what `GET /api/rings/{id}`, `GET /api/metrics`, and `GET /api/benign` already
return — nothing is recomputed on the frontend.

## 1. What problem is this trying to solve?

Stage 11 gave you a network graph you could click around in, but clicking a ring node only
highlighted it — it didn't tell you *why* the agent thinks it's a ring, what evidence it
found, or what the system recommends doing about it. And the project's actual headline
numbers (does this beat a naive baseline? does it lose money? does it wrongly flag real
families?) were still buried in a markdown report (`eval/report.md`) nobody would open
during a demo.

Stage 12 closes both gaps: click a ring, get a readable case file; open the Metrics tab,
see the numbers as charts.

## 2. What was built

```
web/
├── package.json                        # + recharts
└── src/
    ├── App.tsx                         # + Network/Metrics tab toggle, mounts CaseFilePanel
    ├── api/client.ts                   # fixed a type mismatch (see §4)
    ├── components/
    │   └── CaseFilePanel.tsx           # NEW — the slide-over
    └── views/
        └── MetricsView.tsx             # NEW — the four Recharts cards
```

### `CaseFilePanel.tsx` — the slide-over

Opens automatically whenever `App.tsx`'s `selectedClusterId` (already shared between the
graph and the ring list since Stage 11) becomes non-null — no new click-handling was
needed, this stage just added a new listener to state that already existed. It fetches
`GET /api/rings/{id}` and shows, top to bottom:

1. **Verdict banner** — a big red "FLAGGED — likely fraud ring" or green "CLEARED —
   benign" strip, with the member count, the ground-truth label, and the agent's confidence
   percentage.
2. **Recommended action** — a colored pill (Monitor / Hold orders / Manual review / Block)
   for the *policy-decided* action, which can be a downgrade of what the agent itself
   proposed (the panel shows both when that happened, with a one-line reason why).
3. **Evidence subgraph** — a small interactive force-graph, scoped to just this community
   and its directly shared entities, reusing the same node-coloring convention as the main
   Stage 11 graph (red ring member / green benign / grey shared entity).
4. **Shared entities** — chips for every device/card/phone/address/ip_subnet/pincode this
   community shares internally, each showing how many members share it and its evidence
   weight. Device/card chips (the "you deliberately shared this" kind of evidence) are
   styled in red to visually separate them from the weaker, easily-innocent
   phone/address/ip_subnet/pincode chips.
5. **Behavioral evidence** — four to five stat tiles turning the agent's raw tool-call
   numbers into plain English: how many times above the platform's normal
   chargeback/return/refusal rate this group runs, how tightly its accounts were all
   created together, what fraction of its shared evidence is the "deliberate" kind vs. the
   "innocent-compatible" kind, and how many bad orders happened over what time span. Each
   tile turns red only when the number is actually alarming (e.g. event rate ≥2x baseline),
   so a benign cluster's tiles stay calm white/grey.
6. **Agent reasoning** — the verdict's `reasoning` field rendered as an actual paragraph of
   prose, not a JSON dump, followed by its cited evidence bullets and the specific benign
   explanations (family? office? hostel? couple?) it considered and ruled in or out. This
   is the part PLAN.md calls out as what "sells the agent" — a human-readable account of
   *why*, not just a score.

### `MetricsView.tsx` — the four charts

A 2×2 grid of Recharts cards, all reading from one `Promise.all([getMetrics(), getBenign()])`
call so every chart on the page describes the same detection run:

1. **Precision / recall** — grouped bars, ring-level vs. account-level, at the
   money-optimal score threshold.
2. **₹ false-positive-cost sweep** — a line chart of net ₹ (saved minus lost) across every
   possible score threshold, with the actual money-optimal point marked on the curve — this
   is the chart that answers "where should we set the cutoff, in rupee terms, not just
   accuracy terms."
3. **Ring recall: graph vs. baseline** — the punchline chart. Grouped bars at the
   2%/5%/10%-of-accounts review-budget points, showing how much more the graph-based
   detector catches than a transaction-only baseline classifier reviewing the same number
   of accounts.
4. **Benign-cluster results** — a horizontal bar per benign look-alike cluster (family,
   office, hostel/PG, couple, independent), colored red if it was a false positive at the
   money-optimal threshold and green if it was correctly cleared — this is the project
   *owning* its false positives on screen, not hiding them in a report file.

### Wiring into `App.tsx`

Added a small Network/Metrics tab toggle in the header (only visible once a detection run
is `ready`), and mounted `<CaseFilePanel>` once at the bottom of the app as a fixed overlay
— it works the same way regardless of which tab is active, since a ring can be selected
from either the graph or the ranked table.

## 3. Design choices

Same dark theme and two-accent-color system Stage 11 already established (`danger` red /
`safe` green from `tailwind.config.js`) — Stage 12 reused it rather than introducing new
colors, adding only one extra amber accent for the baseline classifier's bars in the
graph-vs-baseline chart (so "graph" and "baseline" are visually distinct from "ring" and
"benign" everywhere else on the page).

## 4. A real bug found while building this (type didn't match reality)

`web/src/api/client.ts`'s `RingCaseFile` type — written in Stage 11, before anyone had
actually rendered a real case file — claimed `verdict.recommended_action` was a string
field on the case file JSON. It isn't. `agent/policy.py::write_case_file()` deliberately
writes only `is_ring`, `confidence`, `evidence`, `benign_explanations_considered`, and
`reasoning` into the persisted `verdict` block; the actual recommended action lives
entirely in the separate `policy_decision` object (`.action` for the bounded/real one,
`.agent_recommended_action` for what the agent itself proposed before any policy
downgrade). This is intentional on the backend side — the agent's raw suggestion is
"advisory only," per `agent/investigator.py`'s own comments — but the frontend type didn't
reflect it.

This went unnoticed through all of Stage 11 because nothing rendered that field until this
stage actually built the panel and read a real case file. Fixed by correcting the type and
pointing the panel's "Recommended action" section at `policy_decision` instead.

## 5. A real rendering bug found via live-browser testing

The first working version of the evidence-subgraph mini force-graph rendered as a
completely empty black box. Cause: `react-force-graph-2d` defaults its `width`/`height` to
the whole browser window when you don't pass them explicitly — inside the slide-over's
small clipped preview box, the simulation was drawing itself far outside the visible area.
Fixed with a `ResizeObserver` on a wrapping `<div>`, the exact same pattern
`NetworkGraph.tsx` already used in Stage 11 for the full-size graph — just applied again at
a smaller scale.

This is a good example of why "does `tsc -b` pass" isn't the same as "does the app work" —
the type-checker had nothing to say about either of these two bugs; only actually clicking
around in a browser caught them.

## 6. Verification performed

Ran `make api` (port 8000, using an already-cached detection run) and `make web` (port
5173) simultaneously, and drove real headless Chromium (Playwright, installed as a one-off
scratchpad tool for this session, same approach Stage 11 used) against the live page:

- **Clicked a suspicious ring** (community #11, RING015, 5 members, score 0.9115): the
  panel opened showing a red "FLAGGED — likely fraud ring" banner at **95% confidence**, a
  red **"Block"** action pill, a populated red-node evidence subgraph, a
  "Card · 5 members · w=0.90" chip, and alarming-red stat tiles — **8.67× the platform's
  normal bad-event rate**, **100% of shared evidence from device/card** (the deliberate
  kind), **100% of members created in the same 14-day window**. The reasoning paragraph
  named the shared card, the elevated event rate, and the account-creation burst as its
  basis for the verdict.
- **Clicked a correctly-cleared benign community** (community #6, ground truth
  `benign:family`, 6/7 members, score 0.1931): the panel opened showing a green
  "CLEARED — benign" banner at **80% confidence**, a neutral **"Monitor"** action pill, a
  green-node evidence subgraph, and calm (not red) "Address"/"Pincode" chips — the *weak*
  kind of evidence. Its stat tiles stayed neutral: event rate only **1.07× baseline** (vs.
  the ring's 8.67×), **0% high-weight entity share** (no device/card sharing at all), no
  account-creation burst. The reasoning paragraph explained that six members sharing one
  address and a common pincode is exactly what a family living together looks like, and
  that nothing else in the evidence pointed toward coordinated fraud.
- **Switched to the Metrics tab**: all four charts rendered with real numbers matching
  Stage 8/10's documented eval — ring-level precision/recall both ~100%, account-level
  recall ~85%, the ₹ sweep correctly marking the money-optimal point at threshold 0.617
  (~₹2.09L net), the graph-vs-baseline bars showing the graph-based detector ahead at every
  review-budget point, and all 19 benign clusters rendered **green** (zero false positives
  at this threshold, matching the eval's documented "0/19 flagged" result).
- **Zero browser console errors** across every screenshot taken.

This is the task's "Show me" bar, met for real: clicking a ring opens a case file with real
agent output — for both a flagged ring and a correctly-cleared benign cluster — and the
metrics view renders all four charts.

## 7. What Stage 13 (polish + demo recording) needs to know

- Long agent responses aren't truncated anywhere in the panel — every real case file seen
  so far reads fine at full length, but a future long-winded model response could look bad
  without a "show more" collapse. Not addressed here since it wasn't observed as a real
  problem.
- The Network/Metrics tab is a plain `useState`, not a real route — there's no way to link
  directly to the metrics view or a specific case file via URL. If that's wanted, this app
  needs actual routing added first (none exists yet).
- If `GET /api/metrics` or `GET /api/benign` fails, `MetricsView` shows one shared error for
  the whole view rather than per-chart errors — deliberate, since all four charts describe
  one run, but worth knowing if a more granular error state is wanted later.
