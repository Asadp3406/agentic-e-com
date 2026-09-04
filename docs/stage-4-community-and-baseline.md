# Stage 4 — Community Detection + the Baseline We're Trying to Beat (in plain language)

This document explains what Stage 4 does, why it's built the way it is, and what we saw
when we ran it. No jargon assumed — if a term needs explaining, it's explained here.

## 1. What problem is this solving?

Stage 3 gave us a graph: customers connected to each other (indirectly, via shared
devices/cards/phones/addresses/IPs/pincodes) with each connection weighted by how
suspicious that kind of sharing is. But a graph on its own is still just a pile of dots
and lines — we haven't yet asked "which dots actually clump together into a group?"

Stage 4 has two separate jobs that don't depend on each other, which is why they're two
separate scripts:

1. **Find the clumps.** [`detect/community.py`](../detect/community.py) runs *community
   detection* — an algorithm that looks at the weighted graph and says "these customers
   are more tightly connected to each other than to anyone else, so treat them as one
   group." Each group is called a **community**. Some will be real fraud rings. Some
   will be Stage 1's honesty-trap clusters (a family, an office, a hostel). Stage 4
   doesn't yet decide which is which — that's Stage 5.
2. **Build the classifier we need to beat.** [`baseline/txn_classifier.py`](../baseline/txn_classifier.py)
   trains a fraud classifier the *old-fashioned* way — one transaction at a time, using
   only that transaction's own details (amount, timing, promo usage, account age). It
   never sees the graph at all. This is what a typical fraud system looks like *before*
   you add network analysis. We need this baseline so that later, when we report "our
   graph approach catches X% of ring accounts," we can honestly say "...compared to Y%
   for a standard transaction classifier" — otherwise the graph number means nothing on
   its own.

Run both with:
```
make detect
```
(after `make data && make resolve && make graph`, since community detection needs
Stage 3's graph, and the classifier needs Stage 1's raw `orders.csv`/`events.csv`.)

## 2. Community detection — finding who clusters with whom

### Algorithm choice: Leiden, with Louvain as a fallback

We use **Leiden** (via the `leidenalg` + `python-igraph` libraries) as the primary
algorithm, with **Louvain** (via `python-louvain`) available as a fallback if Leiden's
libraries aren't installed. Both are well-known "community detection" algorithms that
work by repeatedly trying to move nodes between groups to make the groups more tightly
connected internally than externally.

Why Leiden over Louvain, specifically: Louvain has a known quirk where it can produce a
"community" that isn't actually all connected to itself internally — you can end up with
a group that only looks unified because of how the algorithm's internal steps happened to
run, not because those customers are really linked. Leiden fixes this — every community
it returns is guaranteed to be one connected piece. That matters a lot here, because
Stage 7's investigator agent needs to trust that "these customers are in one community"
actually means something real it can go inspect, not an artifact of the algorithm.

### The knob: `community_resolution`

Community detection algorithms have a "resolution" knob that controls how eager they are
to split things into smaller groups vs. lump them into bigger ones. We use Leiden's *CPM*
variant, whose resolution number works on a very different scale than the more commonly
quoted "modularity" resolution — a value like `1.0` (which sounds like a sensible
neutral default) actually shattered our graph into 1,601 communities of exactly 1 person
each. We had to sweep the value down several orders of magnitude before it did anything
useful:

| resolution | # communities | largest community | rings recovered (≥80% of members together) |
|---|---|---|---|
| 1.0 | 1,601 (all singletons) | 1 | 0 |
| 0.01 | 1,502 | 6 | 2 / 15 |
| 0.005 | 1,468 | 7 | 7 / 15 |
| 0.002 | 1,073 | 10 | 11 / 15 |
| **0.001 (chosen)** | 899 | 10 | 11 / 15 |

We settled on **0.001**, set in `config.yaml`'s `community_resolution`. Like Stage 3's
edge weights, this value is loaded by a function (`load_resolution()`) that **raises an
error** rather than silently running with a meaningless default if the config still has
the placeholder `0` in it — the same "fail loudly, not quietly" policy Stage 3 used for
edge weights.

**Caveat, said plainly: this number is tuned to this specific synthetic dataset's scale
and density.** If you regenerate the data with different volumes or a different edge-
weight scheme, 0.001 may no longer be the right value — you'd want to re-run the same
sweep. It's a hand-tuned dial, not a universal constant.

### What we saw when we ran it at resolution 0.001

- **899 communities total**, 465 of them singletons (a customer connected to nothing
  worth grouping — expected, since roughly half our synthetic customers are
  "independent" legit shoppers with no shared attributes at all).
- **11 of 15 injected rings** land mostly together (≥80% of the ring's members in one
  community) — the top communities by size are visibly dominated by ring members:

  ```
  size=  10  RING004:9
  size=   9  RING005:8
  size=   8  RING010:7, RING008:8, RING013:8, RING002:8
  size=   7  RING001:7
  size=   6  RING011:5
  ```

- **The honesty-trap clusters stayed appropriately fragmented, not falsely merged:**
  - The **hostel** cluster (40 people sharing one pincode — the weakest-weight edge
    type) split across 39 different communities instead of forming one 40-person
    "suspicious" blob. This is the desired outcome: pincode-only sharing is weak
    evidence, and the weighted graph correctly refuses to treat it as grounds for
    grouping people together.
  - The **office** cluster (30 people sharing one IP subnet) similarly split across 25
    communities.
  - The **family** clusters (which share an actual home *address*, a stronger signal)
    clustered somewhat more tightly — 49 family members across 12 communities, several
    families landing together as expected — but still didn't form one giant blob.
  - A handful of rings picked up exactly 1 stray "independent" customer into their
    community (e.g. RING004, RING005, RING010, RING011) — a small amount of noise, not
    a systemic false-merge problem.

### Handling the "giant blob" risk

A real danger with this kind of algorithm: if enough weak, high-degree connections
(pincode, IP subnet) chain unrelated people together through long paths (A shares a
pincode with B, who shares an IP with C, who shares a pincode with Z), you can end up with
one dominant community that swallows most of the customer base. That wouldn't crash
anything — it would just quietly make ring detection useless, because a 7-person ring
hiding inside a 1,000-person "community" is invisible to any per-community statistic
Stage 5 computes.

`detect/community.py` checks for this after every run (`flag_giant_communities`): any
community claiming 20%+ of all clustered customers gets flagged and printed loudly as a
tuning note, rather than the code crashing or — worse — silently proceeding as if nothing
were wrong. At our chosen resolution, **no giant-blob was flagged** (largest community is
10 people, nowhere near the 20% threshold). We also directly tested the flagging logic
itself on made-up data to confirm it correctly fires when a community really is
oversized, independent of whether our specific dataset happens to trigger it.

## 3. The baseline classifier — the bar the graph approach has to clear

### What "structure-blind" means and why it matters

`baseline/txn_classifier.py` trains a standard machine-learning classifier (logistic
regression) to predict, for a single transaction, "does this look like it'll end in a
chargeback, return, or COD refusal?" Its inputs are deliberately limited to what that
*one order* looked like:

- the order amount (and its log, since amounts are skewed)
- whether a promo code was used
- how old the customer's account was when they placed this order
- what hour/day of the week the order was placed
- which number order this was for that customer (1st, 2nd, 50th...)
- how many days since that customer's previous order

None of this touches the graph. The classifier has never heard of a shared device, a
shared card, or a community. This is the point: it represents what a fraud system looks
like *without* network analysis, so we can honestly measure how much the graph-based
approach (Stages 5+) actually adds, rather than comparing our real system against a straw
man.

### Two different "labels," used for two different purposes

This part is worth being precise about, because it's easy to accidentally cheat here
without meaning to:

- **What the model is trained to predict:** whether an individual order has an
  associated chargeback/return/COD-refusal event, from `events.csv`. This is a real,
  observable business outcome — legitimate to train on.
- **What we use to grade the baseline afterward:** whether the *account* is a member of
  one of Stage 1's injected fraud rings, from `ground_truth.csv`. This label is **never**
  shown to the model during training — it's only used afterward, to answer "how good is
  this classifier, that never saw ground truth, at incidentally catching ring accounts?"

### How we turned per-order scores into an account-level decision

The model scores individual orders, but ring detection is a question about *accounts*.
We take each account's single riskiest order as that account's overall risk score, then
flag accounts using a **review budget** — the top 2%, 5%, or 10% of all accounts by risk
score — rather than an arbitrary fixed probability cutoff.

We tried a fixed cutoff first and it didn't produce a meaningful number: because the
model is trained with class-balancing (needed since bad events are rare — only ~5% of
orders), its probability outputs aren't well spread out, so a 0.5 cutoff flagged 89% of
all accounts (meaningless) and a 0.8 cutoff flagged nobody. A review-budget framing —
"you have capacity to review the top N% riskiest accounts this week" — is both more
realistic (that's how fraud-ops teams actually operate) and gives a stable, comparable
number.

### What we found — and why it's not a rock-bottom number

| Review budget | Accounts reviewed | Ring recall | Ring precision |
|---|---|---|---|
| Top 2% | 32 | **26.7%** | 84.4% |
| Top 5% | 80 | **46.5%** | 58.8% |
| Top 10% | 160 | **59.4%** | 37.5% |

At a realistic, tight review budget (2-5%, which is what most real fraud teams actually
have capacity for), **the baseline only catches roughly a quarter to under half of all
ring accounts** — worse the tighter the budget, which is exactly the operating region
that matters in practice.

Worth being honest about *why* it's not worse than that: Stage 1's synthetic rings are
also built with elevated chargeback/return/COD-refusal rates (92% of ring accounts have
at least one bad event, vs. 32% of legit accounts — see `data/generate.py`), so a
transaction classifier isn't flying totally blind; it does pick up "this account's
history looks risky." What it fundamentally cannot do — no matter how it's tuned — is
tell you *which other accounts are run by the same person*, or distinguish "genuinely
risky account" from "innocent family/office/hostel look-alike," because it only ever
looks at one transaction at a time and has never seen who anyone else is connected to.
That gap — not a rock-bottom recall number — is the real baseline story, and it's the
gap Stages 5+ (the graph-based ring scorer) exist to close.

## 4. Summary of what to run and what you'll see

```
make detect
```
runs both scripts in sequence and prints:
- Community detection: method used, resolution, number of communities, giant-blob flags
  (if any), and a look at which communities contain multiple ring members.
- Baseline classifier: transaction-level precision/recall/F1/ROC-AUC on held-out test
  orders, then account-level ring recall/precision at three review-budget operating
  points.
