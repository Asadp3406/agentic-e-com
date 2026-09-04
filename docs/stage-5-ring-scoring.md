# Stage 5 — Ring-Likelihood Scoring (in plain language)

This document explains what Stage 5 does, why it's built the way it is, and what we saw
when we ran it. No jargon assumed — if a term needs explaining, it's explained here.

This is the stage the whole project's credibility rests on. Stage 4 found groups of
customers that cluster together in the graph ("communities"). Some of those groups are
real fraud rings. Some are Stage 1's deliberate honesty traps — a family sharing one
address, an office sharing one WiFi network, a hostel sharing one pincode — that are
**supposed to** look superficially similar to a ring but aren't fraud at all. Stage 4
doesn't tell those two apart; it just finds "who clusters with whom." Stage 5 is where we
actually answer the question: *is this group a coordinated fraud ring, or an innocent
group of people who happen to live/work near each other?*

## 1. What problem is this solving?

If we only asked "do these people share something?", we'd fail immediately — the honesty
traps *deliberately* share things too (Stage 1's family generator even gives a family a
40% chance of sharing a device, exactly like a ring would). Sharing an attribute is not
evidence of fraud on its own. What actually separates a ring from a family, by design of
the synthetic data generator (`data/generate.py`), is a combination of:

1. **What kind of thing they share.** A ring deliberately reuses a device or a payment
   card — that requires effort, it doesn't happen by accident. A family/hostel/office
   shares an address, a pincode, or a WiFi subnet — that happens automatically just by
   living or working in the same place.
2. **How they behave**, not just how they're connected: rings have elevated
   chargebacks/returns/COD-refusals, their accounts get created in a tight burst (days,
   not months apart), and about half of them farm one promo code across the group.
   Honest look-alike clusters have none of that — their accounts trickle in normally
   over the year, and their return/chargeback rate matches everyone else's.

So Stage 5's job is to compute, for every community, a handful of numbers that capture
*which kind* of sharing it is and *how it behaves*, then combine those numbers into one
score. That's `features.py` and `scorer.py`.

Run it with:
```
make detect
```
(after `make data && make resolve && make graph`) — this now runs community detection,
then the baseline classifier, then the ring-likelihood scorer, in sequence.

## 2. The six features (`detect/features.py`)

For every community (a group of customer IDs from Stage 4), we compute:

| Feature | What it measures | Why it separates rings from look-alikes |
|---|---|---|
| **high-weight-edge share** | Of all the evidence connecting members of this group, what fraction comes from a shared *device* or *card* (vs. address/phone/IP/pincode)? | Device/card sharing requires deliberate reuse. A big innocent cluster (40-person hostel sharing a pincode) scores near 0 here even though it's large, because pincode sharing is common-weight, not high-weight. |
| **chargeback/return/COD-refusal density vs. baseline** | This group's own bad-event rate (bad events ÷ their orders), divided by the *dataset-wide* baseline rate. | Rings are built with a 9-16x elevated rate on purpose (35-65% of orders bad, vs. ~4% baseline). This is the single strongest signal, and it's why it gets the highest weight. |
| **timing-burst score** | Were this group's accounts created in a tight window, relative to what you'd expect from that many random people spread across the whole ~1-year dataset window? | Rings are created within a 1-10 day burst. Honesty-trap accounts are created at random times throughout the year — no coordination. |
| **fresh-account ratio** | What fraction of the group falls inside its own densest 14-day creation window? | A slightly different cut of the same idea as timing-burst: "did most of this group arrive together," even if a couple of outlier members joined much later/earlier. |
| **promo/refund concentration** | Of the promo codes actually used by this group's orders, how concentrated are they on one single code (vs. scattered)? | About half of rings farm one specific promo code repeatedly. Everyone else's promo usage (8.8% of orders dataset-wide) is scattered across 5 different codes. |
| **size** | Log-scaled community size. | Included because the spec asks for it as one input — a 2-person pair sharing a card is weaker evidence than a 9-person group doing the same — but deliberately given the *smallest* weight of all six, because size is actually **anti-correlated** with "is a ring" here: rings are 4-9 people, the hostel is 40, the office is 30. |

**Ground truth (`ring_id`, `cluster_tag`) is never read by any of these calculations.**
Those columns exist on the graph's customer nodes purely for Stage 8's eval and this
stage's own diagnostic printout — using them as a scoring input would just be reading the
answer key, not detecting anything.

## 3. Combining features into a score (`detect/scorer.py`)

Each of the six raw features gets squashed into a 0-1 range with a curve appropriate to
that feature (an unbounded ratio like "9x baseline chargebacks" uses a saturating curve
so it doesn't need special-casing; size uses a log then a fixed ceiling), then combined
with fixed weights that add up to 1.0:

| Feature | Weight | Why this weight |
|---|---|---|
| chargeback/return/COD density vs. baseline | **0.35** | Strongest, most reliable signal — a 9-16x gap by construction |
| high-weight-edge share | **0.25** | Second strongest — deliberate device/card reuse is hard to fake by accident |
| timing-burst score | **0.20** | Rings are provably burst-created; honesty traps are not |
| fresh-account ratio | **0.10** | A softer version of burstiness — lower weight to avoid double-counting the same underlying signal |
| promo/refund concentration | **0.07** | Only about half of rings do this, so it's corroborating evidence, not load-bearing |
| size | **0.03** | Deliberately minor, per the spec — included, not driving |

**Why not just threshold the chargeback rate alone and call it a day?** Because that's
exactly what the Stage 4 baseline classifier already does (at the transaction level) and
it only catches 27-59% of ring accounts depending on review budget — its blind spot is
that a handful of unrelated customers who each independently had one bad order would also
show up as "elevated," without being a coordinated ring. Requiring elevated risk **and**
structural evidence (high-weight-edge share) **and** timing coordination together is what
a transaction-only view fundamentally can't do, and it's the reason the graph-based
approach exists at all.

## 4. What we saw when we ran it

```
=== Stage 5: ring-likelihood scoring ===
Communities scored: 899 (from leiden detection)
Showing 201 communities with size >= 3 (of 899 total), ranked by ring-likelihood score
```

The top of the ranked list, score and ground-truth tag side by side (ground truth shown
here only for reporting — it was never an input to the score):

| Rank | Score | Size | Ground truth |
|---|---|---|---|
| 1 | 0.912 | 5 | **RING015** (100% pure) |
| 2 | 0.880 | 8 | **RING008** (100% pure) |
| 3 | 0.878 | 7 | **RING001** (100% pure) |
| 4 | 0.870 | 3 | **RING012** (100% pure) |
| 5 | 0.866 | 5 | **RING009** (100% pure) |
| 6 | 0.825 | 8 | **RING013** (100% pure) |
| 7 | 0.817 | 6 | **RING011** (83% pure) |
| 8 | 0.810 | 10 | **RING004** (90% pure) |
| 9 | 0.804 | 5 | **RING014** (100% pure) |
| 10 | 0.741 | 8 | **RING010** (88% pure) |
| 11 | 0.734 | 8 | **RING002** (100% pure) |
| 12 | 0.697 | 9 | **RING005** (89% pure) |
| 13 | 0.633 | 4 | **RING007** (100% pure) |
| 14 | 0.617 | 4 | **RING003** (100% pure) |
| 15 | 0.582 | 3 | benign:independent |
| 16 | 0.559 | 3 | benign:independent |
| 17 | 0.524 | 3 | benign:hostel_pg |
| ... | ... | ... | ... |
| 167 | 0.111 | 6 | benign:office_cluster |
| 201 | 0.044 | 3 | benign:independent |

**Every one of the 14 ring communities Stage 4 recovered lands in ranks 1-14. Every
single benign look-alike community — family, office, hostel, couple, and independent
legit customers — lands at rank 15 or lower.** The worst-ranked ring (0.617) still clearly
outscores the best-ranked benign look-alike (0.582), and the fraud-ring score curve drops
off sharply (0.91 → 0.62) while benign scores trail off gradually from there — there's a
real gap, not a coin-flip boundary.

```
PASS: every ring (worst rank 14) outranks every benign look-alike (best rank 15).
```

### Where specifically the honesty traps landed

- **Office cluster** (30 people, sharing only an IP subnet — the weakest edge type):
  fragmented by Stage 4 into many small communities, the one that made it into the
  size-≥3 report scored **0.111** (rank 167 of 201) — near the bottom. Its
  `high_weight_edge_share` is 0.00 (no device/card sharing at all) and its event rate is
  baseline, so nothing in the feature set gives it a boost.
- **Hostel/PG cluster** (40 people, sharing only a pincode): also fragmented across many
  tiny communities. The handful that reached size 3 scored in the 0.10-0.52 range —
  below every ring, but a few land higher than the weakest office fragments because a
  couple of coincidentally-clustered hostel residents also happened to share a device by
  chance (Stage 1 assigns devices independently per person, so an occasional accidental
  device match is expected noise, not a systemic problem — see §5 below).
- **Families** (3-6 people sharing an address, 40% chance of also sharing a device):
  scored in the 0.05-0.46 range. The families that share a device score somewhat higher
  (via `high_weight_edge_share`) but their flat event rate and non-bursted creation dates
  keep them well below any ring.
- **Couples sharing a card** (2 people, joint account): scored low (0.27-0.39) — even
  though they share the single highest-weight edge type (card), their event rate is
  baseline and their timing isn't bursted, so two of the three strongest-weighted
  features stay near zero for them.

This is exactly the contrast the project is supposed to demonstrate: **attribute sharing
alone is not enough to rank high** — it takes sharing *plus* the behavioral signature a
real ring has and an innocent cluster doesn't.

## 5. What we found while tuning this (the "if a benign cluster scores high, fix the
feature" loop)

The spec is explicit that this is the heart of the project: if any benign cluster ranked
above a ring, that's not a threshold to nudge, it's a signal that some feature is being
fooled and needs fixing. Here's what we actually found:

- **First version's `high_weight_edge_share` used a raw weight sum, not a share.** A
  large office/hostel fragment that happened to include a couple of coincidental
  device-sharing pairs could rack up a bigger absolute "high-weight" number than a small
  ring just by having more members and more edges overall — even though *proportionally*
  almost none of its evidence was device/card. Switching to a **share of total internal
  evidence weight** (device+card weight ÷ all internal weight) fixed this: a fragment
  with one lucky device match among mostly weak evidence now scores its true proportion
  (often still near 0 if the rest of its links are pincode/IP), rather than getting
  credit for volume it doesn't structurally deserve.
- **`timing_burst_score` needed to be relative to community size, not an absolute day
  count.** A naive "span < N days = bursty" rule would call any 2-person community
  "bursty" almost by definition (two random points in a year are often coincidentally
  close), which would have pushed 2-person benign pairs (like couples) up unfairly. Using
  the *expected* spread of `n` random draws over the dataset window as the yardstick —
  and comparing the community's actual spread against that — corrects for group size, so
  a tight cluster only scores high if it's tighter than chance would predict for a group
  that size.
- **Promo concentration needed to be scaled by adoption rate, not just Herfindahl
  concentration.** A community where exactly one member happened to use exactly one
  promo code (1 use out of, say, 20 orders) is "100% concentrated" by a naive Herfindahl
  calculation, but that's noise, not farming. Multiplying by how much of the group's
  orders actually used a promo at all fixed this — a single incidental use now
  contributes almost nothing, while a ring where most orders use the same code scores
  high on both concentration *and* adoption.

After these three fixes, the exit test passes cleanly with a real margin (0.617 worst
ring vs. 0.582 best benign) rather than a knife-edge threshold — which is itself a useful
signal that the underlying behavioral gap the synthetic data encodes (burst timing +
elevated risk + deliberate high-weight sharing) is genuinely separable, not something we
had to overfit a threshold to find.

## 6. Summary of what to run and what you'll see

```
make detect
```
now runs three scripts in sequence and prints:
1. Community detection (Stage 4): method, resolution, community counts, giant-blob flags.
2. Baseline classifier (Stage 4): transaction-level metrics, then account-level ring
   recall/precision at three review-budget operating points.
3. **Ring-likelihood scoring (Stage 5, new):** every community of size ≥3, ranked by
   score, with its six sub-scores, ground-truth tag, and dominant contributing feature
   shown side by side — plus an explicit pass/fail exit-test line confirming every ring
   outranks every benign look-alike.
