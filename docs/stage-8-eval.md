# Stage 8 — Metrics + Baseline Delta + ₹ Cost (in plain language)

This document explains what `eval/run_eval.py` measures, how to read its numbers, and
where the honest gaps are. PLAN.md's one-line goal for this stage is **"prove it
honestly"** — the point isn't to produce an impressive number, it's to produce a
*defensible* one, including the misses.

## 1. What problem is this trying to solve?

Every earlier stage built a piece of the detection pipeline (entity resolution → graph
→ communities → ring-likelihood score → agent investigator) and eyeballed its own exit
test along the way. Stage 8 is the one place that steps back and asks four questions a
judge or a real fraud-ops lead would actually ask:

1. **How good are we, precisely?** — not "the ring ranked above the benign cluster" but
   actual precision/recall/F1 numbers, at both the community (ring) level and the
   individual-account level, with a confusion matrix.
2. **What's this worth in rupees?** — catching a ring saves money (fewer chargebacks/
   returns/COD refusals); wrongly blocking a real customer costs money too. Which
   threshold on the ring-score actually maximizes net ₹?
3. **Is the graph approach actually better than the boring baseline?** — Stage 4 already
   built a plain per-transaction classifier as a strawman-that-isn't-a-strawman. This is
   the head-to-head that either justifies the whole project's extra complexity or
   doesn't.
4. **Does it cry wolf on the honest look-alikes?** — families, office IP clusters,
   hostel/PG pincode clusters, card-sharing couples. PLAN.md calls these "honesty traps"
   for a reason: a system that flags a family for sharing one address isn't useful no
   matter how good its ring recall is.

## 2. How the eval is wired together

`eval/run_eval.py` doesn't recompute anything Stage 4/5 already built — it *calls* it and
scores the output against `data/ground_truth.csv` (which was never shown to the scorer):

- `detect.community.detect_communities()` — the same communities Stage 4/5 use.
- `detect.scorer.score_communities()` — the same six-feature ring-likelihood score,
  Stage-5-only (Stage 6's GNN feature stays off, per `config.yaml`'s `gnn.enabled: false`
  decision — this eval doesn't second-guess that).
- `baseline.txn_classifier.run_baseline()` — Stage 4's per-transaction classifier, for
  the head-to-head.
- `agent.policy`'s `cost_model` numbers from `config.yaml` (avg order value, chargeback
  cost, false-block cost) — the same ₹ assumptions Stage 7's agent policy already uses,
  so the two ₹ stories in this project (agent-level and eval-level) share one set of
  unit economics instead of two different made-up numbers.

### From "community score" to "account score"

Stage 5's scorer rates *communities*, not individual people. To get precision/recall
over **accounts** (what section 8's confusion matrices need), every member of a scored
community inherits that community's score. An account that isn't in any community of at
least 3 people (too small to be "a group" at all) scores 0 and is never flagged. This
isn't a workaround — it's exactly what the system would do for real: your risk *is* your
group's risk, because the whole point of this project is that a ring is invisible to any
model that only looks at you alone.

## 3. Ring-level and account-level precision/recall/F1

Every community with 3+ members gets one of four ground-truth labels (ground truth is
used **only here**, for grading — never as an input to the score itself):

- **ring** — 80%+ of its members share one `ring_id` (a "pure" ring community).
- **benign** — no ring members at all; majority `cluster_tag` is family/office_cluster/
  hostel_pg/couple.
- **mixed** — spans more than one ring, or a ring mixed with non-ring members.
- **unlabeled** — no clean majority either way.

**Ring-level** precision/recall/F1 is computed only over the ring + benign communities
(mixed/unlabeled are excluded from both sides of the count — deliberately, not silently:
the report says exactly how many were excluded, because scoring an ambiguous group
either way would be measuring something other than "did we call this right").

**Account-level** precision/recall/F1 is computed over all 1,601 accounts, using the
account-score rollup above, against `ground_truth.csv`'s ring/legit label.

Both use one shared threshold — the *money-optimal* threshold from the ₹ sweep (see
next section), so the confusion matrix and the ₹ number describe the same operating
point rather than two different cherry-picked ones.

## 4. The ₹ cost model and threshold sweep

Every score threshold has a real-world trade-off:
- **₹ saved** = (ring accounts you flag) × the ₹ cost of the chargebacks/returns/COD
  refusals they'd otherwise keep generating.
- **₹ lost** = (legit accounts you wrongly flag) × the ₹ cost of blocking/annoying a real
  customer.
- **net ₹** = saved − lost.

`eval/run_eval.py` sweeps *every* score value that actually occurs in the data as a
candidate threshold (not a handful of round numbers), computes net ₹ at each, and picks
the threshold that maximizes it — plotted in `eval/cost_sweep.png` with that point
marked. The shape is intuitive once you see it: net ₹ rises steeply while the highest-
scoring accounts (mostly real rings) get caught with almost no false blocks, flattens out
once most real rings are caught and only false blocks are being added, and would turn
downward if pushed further past that point.

**Read this as directional, not gospel** — the ₹ figures in `config.yaml`'s `cost_model`
(₹1,200 avg order, ₹2,500 per bad event, ₹1,500 per wrongly-blocked customer) are rough
SMB e-commerce assumptions carried over from Stage 7's agent policy, not fitted to any
real merchant's books. The *shape* of the curve (there IS a point where tightening
further stops paying off) is the meaningful, generalizable result.

## 5. HEAD-TO-HEAD: graph approach vs. the Stage-4 baseline

This is the punchline PLAN.md asks for by name. Both approaches are compared at the same
**top-N%-of-accounts review-budget** operating points (2%/5%/10%) — not a probability
threshold, because neither model's raw scores have a meaningful fixed cutoff (see
`baseline/txn_classifier.py`'s docstring; the same reasoning applies to the graph
score). At each budget, both flag the same *number* of accounts, so the comparison is
apples-to-apples on cost.

The baseline classifier only ever sees one transaction at a time — amount, promo usage,
account age, time of day. It has no idea that 7 "different" accounts share one device.
The graph approach sees exactly that. The gap between the two lines in
`eval/head_to_head_recall.png` is the entire value proposition of building a graph in
the first place, made concrete: at a 5% review budget, the graph approach catches
roughly 75% of rings vs. the baseline's ~47% — the same review cost, dramatically more
fraud caught.

## 6. Benign look-alikes: false positives, reported without flinching

Section 5 of the generated `eval/report.md` lists **every single** family, office
cluster, hostel/PG cluster, and card-sharing couple community, its score, and whether it
got flagged at the money-optimal threshold — not a summary count, the actual list, so a
reader can check the claim rather than trust it. If any honesty-trap cluster is
flagged, this report says so plainly (labeled "FLAGGED (false positive)"), because a
system that can't show its false positives isn't trustworthy about its true positives
either.

## 7. What this eval is honest about *not* hiding

Two gaps deliberately are **not** smoothed over by picking a friendlier threshold or a
friendlier universe definition:

1. **Community-detection recall, not scoring recall.** Stage 4's hand-tuned resolution
   recovers 14 of the 15 injected rings as their own identifiable community — one ring's
   members never cluster tightly enough to form a size-3+ community at all, so it's
   structurally invisible to this entire eval (it can't appear in a table of
   communities if it never became a community). The headline "ring-level recall" number
   is over the 14 rings Stage 4 actually surfaces, and the report says so explicitly,
   alongside the honest "of all 15 rings originally injected, X/15 caught end-to-end"
   number, which is lower.
2. **Partial-membership misses inside otherwise-caught rings.** Some accounts that
   belong to a ring Stage 4/5 correctly identifies don't themselves end up inside that
   ring's community (they land in a smaller/different community that doesn't clear the
   threshold). This shows up as account-level recall being noticeably lower than
   ring-level recall — the report explains why the two numbers diverge rather than
   quoting only the more flattering one.

Neither gap is a bug in `eval/run_eval.py` — both trace back to Stage 4's community
detection, and are recorded there for whoever next tunes `community_resolution`.

## 8. How to run it

```
make eval
```
(after `make data resolve graph detect`, which the earlier stages already require). This
runs `eval/gnn_eval.py` (Stage 6's narrower GNN-specific comparison, unchanged) and then
`eval/run_eval.py`, which prints the full summary to the console and writes:
- `eval/cost_sweep.png`
- `eval/head_to_head_recall.png`
- `eval/confusion_ring_level.png`
- `eval/confusion_account_level.png`
- `eval/report.md` (the full write-up, ready to paste into a README or a slide)

Or standalone: `python -m eval.run_eval`.
