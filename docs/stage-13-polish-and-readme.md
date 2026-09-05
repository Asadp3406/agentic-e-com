# Stage 13 — Polish + Demo Verification + README (in plain language)

This document explains what Stage 13 did: it's the last stage in the whole project. Its job
wasn't to build new features — Parts A and B were both functionally complete after Stage 12 —
but to (1) check what "polish" the plan still asked for was genuinely missing, (2) fix the one
real gap that was found, (3) actually click through the whole demo flow in a real browser and
save screenshots of it, and (4) turn the README from a Part-A-only status page into a real
front door for the finished project.

## 1. What problem is this trying to solve?

By the end of Stage 12, the app worked. But three things were still true:

1. Nobody had checked, item by item, whether PLAN.md §B6 Stage 13's checklist ("loading/empty/
   error states, the graph-settling animation, dim-on-select") was actually still missing, or
   whether earlier stages had already quietly built most of it while solving their own problems.
2. The README still read like Part B hadn't been started ("Part B (Stages 10-13...) not yet
   built"), even though Stages 10-12 had already shipped a working FastAPI backend and React
   app. Its Architecture section was a literal `TODO` placeholder.
3. Nobody had pressed every button in the actual demo flow (PLAN.md §B7) in one sitting and
   watched what happens, screenshot by screenshot, the way a person recording a video would.

## 2. Auditing the polish checklist

Rather than assume Stage 13 needed to build loading spinners, empty states, and animations from
scratch, the first step was checking what already existed:

| PLAN.md §B6 ask | Status found |
|---|---|
| Loading states | Already done: `App.tsx`'s run-bar (`running`/`loading-results` phases), `CaseFilePanel.tsx`'s own spinner, `MetricsView.tsx`'s own spinner (Stage 11/12) |
| Empty states | Already done: "Press 'Run detection' to draw the network," "No communities yet — run detection first," "No metrics available — run detection first" |
| Error states | Already done: a dismissable banner in `App.tsx`, an inline error in `CaseFilePanel.tsx`, a shared error message in `MetricsView.tsx` |
| Graph-settling animation | Already done: `cooldownTicks={100}` + a delayed `zoomToFit` call (Stage 11) |
| Dim-on-select | Already done: both the main network graph and the case file's own evidence-subgraph preview dim everything outside the focused cluster and zoom to fit it (Stage 11/12) |

This matches how the project has generally gone: each stage's own verification pass tends to
surface and fix these UX details as it goes, rather than leaving them all for a dedicated
"polish stage" at the end. So Stage 13's actual job was narrower than the plan's wording
suggested — find what's *actually* still missing, not rebuild what already works.

## 3. The one real gap: long reasoning text had no collapse

Stage 12's own write-up (`docs/stage-12-case-file-and-metrics.md`) named this directly:

> "the case file's `verdict.reasoning` prose and `evidence`/`benign_explanations_considered`
> arrays are rendered directly (no truncation) — on a long-winded model response this could run
> long; Stage 13's polish pass may want a 'show more' collapse if that turns out to look bad on
> a real long case file, not addressed here since every real case file seen so far... reads fine
> at full length."

This was the one item on the whole checklist that was both (a) genuinely unaddressed and (b)
explicitly flagged by name as something the next stage should look at. Fixed by adding a small
`Collapsible` component to `web/src/components/CaseFilePanel.tsx`: any reasoning text over 420
characters now truncates to a preview with a "Show more" / "Show less" toggle; anything shorter
(every real case file seen through Stage 12) renders exactly as before, unchanged. `npx tsc -b`
still passes clean in strict mode after the change.

## 4. Verifying the demo flow for real

The actual point of a "polish + demo" stage is making sure the thing you're about to record
actually works, not just that the code compiles. So the full PLAN.md §B7 demo flow was driven
end to end in a real headless-Chromium browser (Playwright, installed as a one-off tool in the
session scratchpad, the same approach Stages 11/12 used — not added to `web/package.json`),
against the already-running backend (`make api`, port 8000, serving an already-cached run from
an earlier session) and frontend (`make web`, port 5173):

1. Land on the empty dashboard.
2. Click "Run detection" — the network graph draws and settles.
3. Click the top-ranked ring (community #11, ground truth RING015, score 0.9115) — the graph
   dims/zooms to it, a case file opens: 95% confidence, `block` action, evidence subgraph, red
   "Card" chip, alarming stat tiles.
4. Click a benign family (community #6, ground truth `benign:family`, 6/7 members) — case file
   shows 80% confidence, `Monitor` action, green banner, calm stats.
5. Switch to the Metrics tab — all four charts render with real numbers matching
   `eval/report.md` exactly (precision/recall, ₹ sweep with the money-optimal point marked,
   graph-vs-baseline recall bars, all 19 benign clusters' scores).

Zero browser console errors at any step. Screenshots from this run are saved under
`docs/screenshots/` and embedded directly in the new README's demo-flow section.

### A real finding while picking a benign example to screenshot

The screenshot script's first attempt picked the first row with a "cleared" status chip, which
turned out to be community #71 — and its case file, loaded from disk, said `is_ring: true`
(confidence 0.85), contradicting its own "cleared" label in the ring list. This is **not a new
bug**: it's the exact finding Stage 9 already documented and reported honestly — "the hardest
benign look-alike got different verdicts across two real runs" — still sitting on disk from
whichever earlier session's real LLM call produced it. Checking all six cached case files found
a second one (community #24) with the same kind of stale, over-eager verdict.

The right move here was **not** to quietly delete or re-investigate these — doing that would
erase real evidence of a documented reliability gap and make the project look more polished
than it honestly is. Instead, community #6 (confirmed via `GET /api/rings` to genuinely be
`benign:family (6/7)`, and via its own case file to have `is_ring: false`) was picked as the
demo's benign example instead, and the README's demo-flow section now explicitly warns that
clicking community #71 specifically may show a flagged result — so a future screen recording
doesn't stumble into it and think the app is broken.

## 5. The README overhaul

The previous README was accurate for a Part-A-only project but stale: it still said "Part B...
not yet built" and had a literal `TODO` where the architecture diagrams should go. Rewritten
section by section:

- **Overview** — reframed around the finished system (engine + dashboard), not just the engine.
- **How to run** — explicit numbered steps, including the two-terminal requirement (`make api`
  in one, `make web` in the other) that the old README's flat command list didn't call out.
- **Demo flow** (new) — a screenshot-illustrated, step-by-step walkthrough matching PLAN.md
  §B7, written so it can be read aloud while screen-recording. Includes the community #71
  caveat from §4 above.
- **Architecture** — both real ASCII diagrams (PLAN.md §4 engine, §B1 web), replacing the TODO.
- **Metrics** — kept Stage 8's numbers as-is (nothing scoring-related changed this stage, so no
  need to re-run `make eval`), with a pointer to the Metrics tab where the same numbers render
  live.
- **What broke and what I did** — kept every prior entry, and added the two Stage 12 web bugs
  (the `recommended_action` type/data mismatch, the blank evidence-subgraph sizing bug) that
  weren't yet in the README's own version of this section.
- **Failure handling** — kept the four PLAN.md §10 items, added two web-layer ones now that
  Part B is in scope: the `GET`-before-`POST /api/run` 409 empty state, and the case-file
  panel's own loading state for slow first-open LLM calls.
- **Limitations** — turned from a checklist into prose, folded the agent-reliability caveat and
  the GNN decision into it, and removed the stale "Part B not yet built" line.

## 6. What Stage 13 deliberately did NOT touch

No Python engine code, no `config.yaml`, no re-run of `make eval` or `make demo`. Nothing in
this stage's scope (UI polish, verification, documentation) touches scoring, the agent, or the
cost model, so every number already in `eval/report.md` and the README's Metrics section is
still current and accurate — there was no reason to regenerate them.

## 7. How to reproduce the screenshots

```
make api    # terminal 1, port 8000
make web    # terminal 2, port 5173
```

Then drive a headless browser (Playwright or similar) through: load `localhost:5173` → click
"Run detection" → wait for the graph to settle → click the top-ranked table row → wait for the
case file to load → close it → click a `cleared` row you know to be a genuine benign example
(community #6 in this dataset/seed, or re-derive one via `GET /api/rings` + `GET /api/rings/{id}`
the way §4 above did) → close it → click the "Metrics" tab. Screenshot at each step.
