# Stage 3 — Building the Graph (in plain language)

This document explains what Stage 3 does, why it's built the way it is, and what we
saw when we ran it. No jargon assumed — if a term needs explaining, it's explained
here.

## 1. What problem is this solving?

Stage 2 gave us clean, trustworthy answers to questions like "do these two customers
really share the same address?" or "do they really use the same device?" — regardless
of how differently the raw data was typed.

But a pile of yes/no answers isn't something we can *look at* or reason about yet.
What we actually want to see is a **map of who's connected to whom, and how
strongly** — so that a tightly-knit group of accounts secretly sharing devices and
cards visibly stands out from, say, a family that innocently shares one home address.

**Stage 3's job:** turn Stage 2's clean matches into an actual graph — dots
("nodes") connected by lines ("edges") — where the connections are also *weighted*,
so a strong, suspicious link (same device) looks different from a weak, everyday one
(same neighborhood pincode).

## 2. What was built

Two scripts:
- [`graph/weights.py`](../graph/weights.py) — decides how much each *type* of shared
  attribute should "count."
- [`graph/build.py`](../graph/build.py) — actually builds the graph using those
  weights.

Run it with:
```
make graph
```
(after `make data && make resolve`, since it needs Stage 1's raw data and Stage 2's
resolved entity IDs).

## 3. The core idea: not all shared things are equally suspicious

Imagine two customers share a home address. Could be a fraud ring using one fake
address for 7 accounts. Could also just be two roommates, or a married couple. On its
own, "same address" doesn't tell you much.

Now imagine two customers share a **device fingerprint** — literally the same phone
or browser used to create both accounts. That's very hard to explain innocently.
Nobody accidentally uses a stranger's exact phone to sign up for a shopping account.

So the two links are not equally strong evidence, even though both are "a shared
attribute." Stage 3's central idea is: **weight each type of shared attribute by how
rare/deliberate it would be for that sharing to happen by accident.** The rarer it
is, the more it counts as evidence of coordinated behavior (i.e., a ring), rather
than of two ordinary people innocently overlapping.

### The scale we chose, and why

| Shared attribute | Weight | Why |
|---|---|---|
| Device | **1.0** (highest) | Using the *same physical phone/browser* to run two "different" accounts is essentially never an accident. |
| Card / UPI | **0.9** | Same payment instrument funding two accounts is almost always deliberate — slightly below device only because legit card-sharing exists (a couple, a family card). |
| Phone | **0.7** | A real, hard-to-fake link, but households do share one landline/number, so it's a notch below device/card. |
| Address | **0.6** | Stage 2's fuzzy-matched "same physical home" — families, couples, and hostel-mates share this innocently just as often as rings do deliberately. |
| IP subnet | **0.2** | Covers a whole office/ISP block — hundreds of unrelated people can be behind the same one. |
| Pincode | **0.1** (lowest) | Covers a whole neighborhood. Practically everyone nearby "shares" this. Kept mainly as background noise to contrast real signal against. |

These numbers are **hand-set, informed priors** — not something learned from data.
The point isn't that "device" is *exactly* 1.0 and "card" is *exactly* 0.9; the point
is the *ordering and the gap*: device/card should count far more than
pincode/IP-subnet. They live in [`config.yaml`](../config.yaml) so they can be tuned
later without touching any code.

**Why this matters downstream:** without this weighting, a group of strangers who
just happen to live in the same big city block (shared pincode) would look exactly as
"connected" as a fraud ring secretly reusing one device across 7 accounts. That would
either bury real rings in noise, or wrongly flag huge swaths of innocent people as
suspicious. Weighting lets the *real* signal (device/card reuse) stand out from the
*background hum* (shared pincode/IP) that's true of almost everyone.

## 4. How the graph is shaped

We built the graph as **two kinds of dots connected to each other** — not just
customer-to-customer lines. Specifically:

- **Customer nodes** — one per customer.
- **Attribute nodes** — one per distinct device, card, phone, address, IP subnet, or
  pincode (using Stage 2's canonical IDs, so "the same device typed differently"
  is already collapsed into one node).

A line ("edge") connects a customer to an attribute node whenever that customer
uses/shares that attribute. Two customers are *indirectly* linked when they both
connect to the same attribute node.

**Why not just draw customer-to-customer lines directly (skipping the attribute
dots)?** We considered it, but kept the customer↔attribute structure instead, for two
reasons:

1. **We'd lose the "how."** If we draw a direct line from Customer A to Customer B
   labeled "connected," we can no longer say *which* device or card connected them.
   Later, when an AI agent investigates a suspicious group and needs to explain
   *why* it's suspicious ("these 7 accounts all used the exact same phone"), it
   needs that detail preserved. Keeping the attribute as its own dot preserves it
   automatically.
2. **It avoids a combinatorial mess.** If 200 unrelated customers all happen to share
   one weak/common attribute (like a pincode), connecting them all directly to each
   other would require ~20,000 lines just for that one pincode. Keeping the pincode
   as a single shared dot means just 200 lines — one per customer — instead.

There's also a helper, `customer_projection()`, that *can* collapse the graph down to
direct customer-to-customer lines if a later stage prefers that view — but the graph
this stage produces and the Makefile builds is the two-kind-of-dots version.

## 5. What extra information each dot and line carries

It's not just "connected, yes/no" — each dot and line remembers useful details for
later stages:

- **Customer dots** remember: when the account was created, and how many
  chargebacks/returns/COD-refusals that customer has had. (Stage 4 will use this to
  ask "does this whole cluster of customers have suspiciously high chargeback
  rates?") They also remember Stage 1's ground-truth ring/family labels — but those
  are only for us to check our work with later; the actual detection logic in Stage
  4/5 is not allowed to peek at them, since that would be cheating.
- **Lines (edges)** remember: which *type* of attribute they represent (device,
  card, etc.) and its weight.

## 6. What we saw when we ran it

Running `make graph` on the full synthetic dataset produced:

**Overall size:**

| Node type | Count |
|---|---|
| customer | 1,601 |
| phone | 1,601 |
| ip_subnet | 1,572 |
| device | 1,561 |
| card | 1,542 |
| address | 1,530 |
| pincode | 802 |

**Total: 10,209 dots, 9,606 lines** (1,601 lines of each attribute type — in this
synthetic dataset every customer has exactly one device, one card, one phone, one
address, and one IP, so the line counts come out even; that won't necessarily stay
true once real multi-device/multi-order customers are modeled).

**The real test — does a fraud ring actually look different from an innocent
family?** We picked one known fraud ring and one known innocent family from Stage 1's
data and looked only at the lines connecting their own members to each other:

**Ring RING001 (7 members):**
- Shared **device** (weight 1.0) — connects 5 of the 7 members
- Shared **card** (weight 0.9) — connects 5 of the 7 members
- Total internal weight: **1.9**, and **100% of it** comes from the two
  highest-weight (device/card) link types.

**Family "family_0" (3 members):**
- Shared **address** (weight 0.6) — connects all 3 members
- Shared **pincode** (weight 0.1) — connects all 3 members (they live in the same
  area, unsurprisingly, since they share an address)
- Total internal weight: **0.7**, and **0% of it** comes from device/card.

This is exactly the contrast we want to see: the fraud ring is held together by rare,
deliberate, high-weight shares (same device, same card) — the kind of thing that
doesn't happen by accident. The innocent family is held together only by the
low/medium-weight shares you'd expect from people who simply live together. If we
hadn't weighted the edges, both groups would have looked equally "connected" — the
family even has *more* raw shared attributes (2 shared entities same as the ring, but
try a bigger family and it'd have more members touching the same address/pincode),
which is exactly why raw connection-counting isn't good enough and the weighting is
the point of this whole stage.

## 7. What Stage 4 gets from this

Stage 4 (finding clusters of tightly-connected customers, a.k.a. "community
detection") doesn't need to touch any raw data or Stage 2 output directly — it just
loads the graph this stage built (`graph.build.build_graph()`) and looks at which
customers are pulled together by heavy, high-weight connections. The event-count
attributes already attached to each customer dot (chargebacks/returns/COD-refusals)
are there waiting for Stage 4's next step: turning "these customers are tightly
connected" into "...and they also behave like a ring, not just a household."
