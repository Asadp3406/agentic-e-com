# Stage 6 (optional, advanced tier) — GNN Anomaly Layer (in plain language)

This document explains what Stage 6 tried, what we measured, and why the result is
**disabled by default**. Unlike every other stage in this project, Stage 6's write-up
ends in "we built it, measured it honestly, and it made things worse" — which is a real,
useful outcome, not a failure to hide. PLAN.md marks this stage optional/advanced and
says explicitly: cut it if it doesn't help, and say so in the README. This is that.

## 1. What problem is this trying to solve?

Stage 5's `scorer.py` is six numbers a human picked (chargeback rate, device/card
sharing, timing burst, etc.), combined with weights a human also picked. That's honest
and explainable, but it can only ever catch the specific patterns those six features
were designed to look for. Stage 6 asks: can a model that *learns* what a "normal" node's
graph neighborhood looks like — without ever being told which nodes are rings — catch
coordination that the hand-crafted features miss?

## 2. The approach: unsupervised graph autoencoder, not supervised classification

PLAN.md's Stage 6 spec offers a choice: a supervised GraphSAGE classifier trained
directly on `ring_id`, or an unsupervised graph autoencoder. We picked the
**unsupervised autoencoder**, for the same reason Stage 5's scoring weights were
hand-picked rather than fit to `ring_id`: every other stage in this project treats
ground truth as *eval-only*, never as a training input, specifically so the final numbers
mean "this generalizes to structure we didn't tell it about," not "this memorized the
answer key." A supervised model trained on `ring_id` would need a careful train/test
split to keep that promise — and even then would really be learning "what THIS
synthetic generator's rings look like," a narrower and more brittle signal than
"what does anomalous graph structure look like."

**How it actually works, in plain terms:** every node in the graph (each customer, and
each device/card/phone/address/ip_subnet/pincode entity) gets compressed by a small
neural network (2 layers of GraphSAGE, which looks at a node plus a sample of its
neighbors) into a short list of numbers — an "embedding." The model is trained with one
job: given a node's embedding, predict which other nodes it's actually connected to in
the graph. A node whose embedding makes that prediction easy is "structurally
unsurprising" — it looks like most other nodes with that kind of neighborhood. A node
whose embedding makes that prediction hard is structurally unusual relative to
everything else in the graph. We use that prediction-error as a per-node "anomaly
score," and average it across a community's members to get one anomaly score per
community — exactly the same shape of output Stage 5's other six features produce, so
it can be added to the score as a 7th feature.

**What the model sees per node**, before any learning happens: how many edges it has,
how much total edge-weight, what fraction of that weight is device vs. card vs. phone
vs. address vs. ip_subnet vs. pincode, whether it's a customer or an entity node, and
(customers only) how many bad events it has and how old the account is. It never sees
`ring_id` or `cluster_tag` — see `detect/gnn.py`'s module docstring for the full feature
list and reasoning.

Run it standalone with:
```
python -m detect.gnn
```
(after `make data resolve graph`) — trains the model and prints the top-15 communities
by raw GNN anomaly score alone (no fusion with Stage 5's features), for inspection.

## 3. The eval: does it actually help? (`eval/gnn_eval.py`)

The exit test PLAN.md asks for is specific: **does the GNN improve ring recall at the
same false-positive rate, compared to Stage 5 alone?** Not "does it look interesting" —
does it change the actual catch-rate at a review budget you'd realistically operate at.

**Method:** every community Stage 4 finds (size ≥ 3) gets a ground-truth label for eval
purposes only — "ring" (≥80% of members share one `ring_id`, same rule Stage 5's own
PASS/FAIL check uses), "benign" (a family/office/hostel/couple look-alike), or excluded
if mixed/unlabeled. We rank communities by score (highest first) — once with Stage 5's
original six features, once with a 7th `gnn_anomaly` feature fused in — and sweep a
review-budget cutoff (top-N communities flagged) from 1 up to everything, exactly the
same top-N-by-budget framing Stage 4's baseline classifier and Stage 5 already use
instead of a fixed probability threshold. At every false-positive-rate level the
Stage-5-only ranking actually reaches, we compare the ring recall each ranking achieves
there.

**Fusion weight:** the 7th feature gets `config.yaml`'s `gnn.fusion_weight` (0.15
by default), and the original six weights are scaled down proportionally (×0.85) so
everything still sums to 1.0 — this keeps their *relative* importance to each other
unchanged (chargeback rate is still the strongest of the six) rather than re-picking
seven weights by hand.

## 4. What we saw when we ran it — before/after ring recall

```
=== Stage 6 eval: GNN-fused vs Stage-5-only ring recall at equal FP rate ===
Universe: 14 pure/near-pure ring communities, 180 benign look-alike communities (size >= 3)
GNN autoencoder final reconstruction loss: 1.0879

 FP rate   Stage5-only recall   Stage5+GNN recall  delta
------------------------------------------------------------
    0.0%               100.0%               85.7%  -14.3%  <-- worse
    0.6%               100.0%               92.9%   -7.1%  <-- worse
    1.7%               100.0%              100.0%   +0.0%
  ... (178 further FP-rate levels omitted, both curves unchanged/saturated)

VERDICT: GNN-fused HURTS ring recall at equal FP rate. Keep config.yaml gnn.enabled: false.
```

At the tightest, most realistic operating point — **zero benign false positives** —
Stage 5 alone already catches **all 14 of 14** rings (this is the same "worst ring rank
14, best benign rank 15" result documented in Stage 5's write-up). Adding the GNN
feature actively **hurts** at that same operating point: it drops to 12/14 (85.7%). It
only catches back up to 100% once you're willing to accept ~1.7% of benign look-alikes
as false positives — i.e. the GNN feature adds noise at the operating point that
matters, and only stops hurting once you've already loosened the budget enough that it's
no longer contributing anything either.

**Concretely, what went wrong:** with the GNN feature on, a 3-person `benign:independent`
community jumps to rank 13 — ahead of a real ring (RING007, rank 14) — because that
community's raw `gnn_anomaly` sub-score is 0.74, higher than most of the actual rings'
GNN sub-scores. You can reproduce this yourself:
```
# in config.yaml, temporarily set gnn.enabled: true, then:
python -m detect.scorer
```
and look at rank 13 vs 14 in the printed table (`gnn` column, `top-feature` column shows
`high_weight_edge_share` for rank 13, meaning even *that* community's own strongest
signal isn't the GNN score — the GNN score is just along for the ride, dragging it up).

**Why this makes sense, not just "the GNN is bad":** the six hand-crafted features are
each purpose-built to isolate one specific behavioral or structural fingerprint that
Stage 1's generator gives rings and *only* rings (deliberate device/card reuse, burst
account creation, elevated bad-event rate). The autoencoder's "anomaly" signal is a much
more general notion — "this node's local neighborhood is statistically unusual in the
graph" — which doesn't specifically target coordination. A handful of benign clusters
end up structurally unusual for reasons that have nothing to do with fraud (e.g. an
`independent` cluster with an atypical mix of edge types), and the autoencoder has no
way to distinguish "unusual because it's a ring" from "unusual for some other reason."
The six hand-crafted features, precisely because they were built by looking at what
actually makes Stage 1's rings different, don't have that failure mode.

## 5. Decision: disabled by default

`config.yaml`'s `gnn.enabled` is **`false`**. `detect/scorer.py` behaves exactly as it
did at the end of Stage 5 — six features, same weights, same PASS result (ring worst
rank 14, benign best rank 15) — unless this flag is flipped. The code for the fused path
is fully wired and tested (`python -m detect.scorer` with `gnn.enabled: true` runs end
to end and produces the ranking shown above), so re-enabling it is a one-line config
change if a future dataset or feature-engineering pass changes this result — but on
*this* dataset, with *this* feature set, the honest answer is that it doesn't help, and
per PLAN.md's own cut rule, we're saying so here rather than shipping it on anyway.

## 6. Time spent

Comfortably inside the ~1.5-day timebox PLAN.md allows for this stage — most of the time
went into (a) confirming PyTorch/PyTorch-Geometric actually install and import cleanly
on this repo's Python 3.14 venv (they do, as of torch 2.14.0 / torch-geometric
2.8.0.post1 — not a given, since PyTorch support for brand-new Python versions usually
lags), and (b) tracking down a subtle non-determinism bug: `torch.manual_seed()` alone
does **not** make training reproducible, because `torch_geometric`'s `negative_sampling()`
falls back to Python's stdlib `random.sample()` internally, which isn't covered by
`torch`'s RNG seed. Fixed by seeding `random`, `numpy`, and `torch` together, plus
`torch.use_deterministic_algorithms(True)` and single-threaded execution (multi-threaded
CPU kernels have non-fixed floating-point reduction order that compounds over training
epochs) — verified by running training twice in one process and via two separate
`python -m detect.gnn` invocations and diffing the final loss to full float precision.
This matters because every other stage in this project promises "same seed, same
output," and a Stage 6 that silently didn't honor that would make its own eval numbers
untrustworthy.

## 7. How to reproduce

```
make data resolve graph        # if not already run
python -m detect.gnn           # standalone: trains GAE, prints raw anomaly ranking
python -m eval.gnn_eval        # the before/after comparison in section 4
make gnn-eval                  # same, via Makefile
```
