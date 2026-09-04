"""Stage 5: per-community ring-likelihood features.

WHAT THIS DOES
--------------
Takes one community (a list of customer_ids from Stage 4's `detect_communities`) and
computes a small set of numeric features that describe *how that community behaves*,
not just *how big it is* or *what it shares*. `scorer.py` combines these into a single
ring-likelihood score.

WHY THESE FEATURES AND NOT OTHERS
----------------------------------
Stage 1's synthetic generator (see `data/generate.py`) draws a hard line between rings
and benign look-alikes, and it is NOT "do they share an attribute" -- families, the
office cluster, and the hostel/PG cluster all deliberately share devices, IPs, or
addresses too (the "honesty trap"). The actual line the generator draws is:

    rings = coordinated attribute sharing (device/card, the rare/deliberate kind)
            + elevated loss-causing event rate
            + tight account-creation burst
            + (~50% of rings) a farmed promo code

    benign = attribute sharing (often the weak/innocent kind: address/ip/pincode, but
             sometimes device/card too) + baseline event rate + normally-spread
             account creation

So a feature like "do they share *anything*" is useless (both sides do it) and a
feature like "big community" is actively misleading (the hostel/office clusters are
much bigger than any ring). The six features below are chosen to isolate exactly the
*behavioral* signals that only rings have, while keeping sharing-based features focused
on *which kind* of attribute is shared (rare/deliberate vs common/innocent) rather than
sharing per se:

  1. high_weight_edge_share  -- rings deliberately reuse device/card; benign clusters
     that share *do* sometimes use device (family, 40% chance) but families/office/
     hostel/couples never coordinate on both, and this feature is a share (0-1) of
     total internal evidence weight, not a raw count, so a big low-weight cluster
     (hostel/office) doesn't win on volume alone.
  2. event_rate_ratio  -- the single strongest behavioral signal per the generator
     (LEGIT_EVENT_RATE=0.04 vs RING_EVENT_RATE 0.35-0.65): community's combined
     chargeback+return+cod_refusal rate over its own orders, relative to the GLOBAL
     baseline rate (computed once over all customers, not per-community, so it's a
     fixed yardstick).
  3. timing_burst_score -- rings are created in a 1-10 day burst; benign clusters are
     created uniformly across the ~1-year simulation window. Measured as how much
     tighter the community's created_at spread is than a random-same-size sample's
     spread would be, so it's a *relative* burstiness, not an absolute day count (a
     size-2 couple is trivially "bursty" by chance -- comparing against same-size
     random draws corrects for that).
  4. fresh_account_ratio -- fraction of members whose account is still within its own
     burst window (using each member's first order as "activity start" and checking
     how many members' created_at cluster within a short window of each other) --
     kept as a distinct, simpler signal from timing_burst_score: this one asks "are
     most members new," not "were they created at the same time." A community that
     re-uses a device over YEARS (long-lived shared family tablet) scores low here
     even though it might still share a device.
  5. promo_concentration -- rings farm ONE promo code (~50% of rings, generator's
     PROMO_CODES); legit promo usage (8.8% of all orders, 5 codes) is scattered.
     Herfindahl-style concentration of promo codes actually used by the community,
     scaled by how much of the community uses a promo at all (a community where
     nobody uses promos scores 0 here, not undefined/1).
  6. size -- included per the spec as a feature, but deliberately NOT a driver: it's
     log-scaled and given the smallest weight in scorer.py specifically because size
     alone is anti-correlated with "ring" in this dataset (rings: 4-9 members; hostel:
     40, office: 30).

NOT USED, ON PURPOSE
---------------------
`ring_id` / `cluster_tag` on customer nodes are ground-truth/debug labels (per
graph/build.py's docstring) -- reading them here would be cheating (the score would
just be reading the answer key). This module never touches them; `scorer.py`'s
diagnostic printout is the only place ground truth is allowed to appear, and only
for *reporting* how the (blind) score did, never as an input to it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import pandas as pd

from graph.build import internal_edges

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

HIGH_WEIGHT_EDGE_TYPES = {"device", "card"}

# Members created within this many days of each other count as part of the same
# "fresh burst" for fresh_account_ratio. Generator bursts span 1-10 days (see
# data/generate.py build_fraud_ring), so a window a bit wider than the max ring burst
# still comfortably separates it from benign clusters (created uniformly over ~1 year).
FRESH_BURST_WINDOW_DAYS = 14


@dataclass
class GlobalBaseline:
    """Dataset-wide reference rates, computed once and reused for every community so
    each community's features are relative to a fixed yardstick, not to each other."""

    event_rate: float  # (chargebacks + returns + cod_refusals) / total orders, global
    created_at_span_days: float  # full dataset created_at range, for burst normalization


@dataclass
class CommunityFeatures:
    community_index: int
    size: int
    members: list[str]
    high_weight_edge_share: float
    event_rate_ratio: float
    timing_burst_score: float
    fresh_account_ratio: float
    promo_concentration: float
    size_feature: float
    # Raw counts kept alongside the normalized features, useful for the scorer's
    # explain output and for debugging a surprising score.
    n_orders: int
    n_bad_events: int
    community_event_rate: float


def compute_global_baseline(
    customers: pd.DataFrame, orders: pd.DataFrame, events: pd.DataFrame
) -> GlobalBaseline:
    total_orders = len(orders)
    total_bad_events = len(events)
    event_rate = (total_bad_events / total_orders) if total_orders else 0.0

    created = pd.to_datetime(customers["created_at"])
    span_days = max((created.max() - created.min()).total_seconds() / 86400.0, 1.0)

    return GlobalBaseline(event_rate=event_rate, created_at_span_days=span_days)


def _high_weight_edge_share(graph: nx.Graph, members: list[str]) -> float:
    """Share of internal evidence weight (edges connecting >=2 members of this
    community) coming from device/card, the two edge types that require deliberate
    reuse rather than innocent co-location. 0.0 if the community shares nothing
    internally (e.g. a singleton, or a Leiden artifact with no real internal edges)."""
    edges = internal_edges(graph, members)
    if not edges:
        return 0.0
    total_weight = sum(w for _, _, w, _ in edges)
    if total_weight <= 0:
        return 0.0
    high_weight = sum(w for _, edge_type, w, _ in edges if edge_type in HIGH_WEIGHT_EDGE_TYPES)
    return high_weight / total_weight


def _event_rate_ratio(
    members: list[str], orders_by_customer: dict, events_by_customer: dict, baseline: GlobalBaseline
) -> tuple[float, int, int, float]:
    """Community's own bad-event rate over its own orders, relative to the global
    baseline rate. Returns (ratio, n_orders, n_bad_events, community_rate).

    A ratio of 1.0 means "exactly baseline"; >1.0 means elevated. Capped isn't applied
    here (scorer.py handles squashing into [0,1]) so the raw ratio stays inspectable.
    Communities with zero orders get ratio 0.0 (no evidence of elevated risk, not an
    undefined spike) rather than dividing by zero.
    """
    n_orders = sum(orders_by_customer.get(m, 0) for m in members)
    n_bad_events = sum(events_by_customer.get(m, 0) for m in members)
    if n_orders == 0:
        return 0.0, 0, 0, 0.0
    community_rate = n_bad_events / n_orders
    if baseline.event_rate <= 0:
        return 0.0, n_orders, n_bad_events, community_rate
    return community_rate / baseline.event_rate, n_orders, n_bad_events, community_rate


def _timing_burst_score(
    members: list[str], created_at_by_customer: dict, baseline: GlobalBaseline
) -> float:
    """How tightly clustered this community's account-creation timestamps are,
    relative to what you'd expect from `len(members)` accounts spread uniformly across
    the whole dataset window. Returns a value in [0, 1]: 0 = spread as wide as (or
    wider than) a uniform random sample this size would typically be; 1 = all members
    created at effectively the same instant.

    Uses the *expected* spread of `n` uniform-random draws over the dataset window
    (span * (n-1)/(n+1), the expected range of n iid uniform draws) as the denominator,
    so a community of 2 isn't unfairly called "bursty" just because two random points
    are naturally close together relative to n=40.
    """
    if len(members) < 2:
        return 0.0
    timestamps = [created_at_by_customer[m] for m in members if m in created_at_by_customer]
    if len(timestamps) < 2:
        return 0.0
    timestamps.sort()
    actual_span_days = (timestamps[-1] - timestamps[0]).total_seconds() / 86400.0

    n = len(timestamps)
    expected_span_days = baseline.created_at_span_days * (n - 1) / (n + 1)
    if expected_span_days <= 0:
        return 1.0 if actual_span_days == 0 else 0.0

    ratio = actual_span_days / expected_span_days
    return max(0.0, 1.0 - min(ratio, 1.0))


def _fresh_account_ratio(members: list[str], created_at_by_customer: dict) -> float:
    """Fraction of members that fall inside the community's own densest creation
    window (a sliding FRESH_BURST_WINDOW_DAYS-day window, picked to maximize coverage).
    This is deliberately a *different* cut than timing_burst_score: it asks "what
    fraction of this community arrived together," not "how tight is the full spread."
    A community with one old founder account and many fresh burst-created accounts
    still scores high here even though its full timestamp range is wide.
    """
    timestamps = sorted(created_at_by_customer[m] for m in members if m in created_at_by_customer)
    if not timestamps:
        return 0.0
    if len(timestamps) == 1:
        return 1.0

    window = pd.Timedelta(days=FRESH_BURST_WINDOW_DAYS)
    best_count = 0
    left = 0
    for right in range(len(timestamps)):
        while timestamps[right] - timestamps[left] > window:
            left += 1
        best_count = max(best_count, right - left + 1)
    return best_count / len(timestamps)


def _promo_concentration(members: list[str], promo_codes_by_customer: dict) -> float:
    """Herfindahl-style concentration of promo codes used across the community's
    orders, scaled by promo-adoption rate. A community where everyone uses the SAME
    single promo code scores near 1.0; a community that scatters across several codes
    (or matches the dataset's natural ~8.8% scattered usage) scores low; a community
    that uses no promos at all scores 0.0 (no evidence either way, not "maximally
    suspicious" nor "maximally clean").
    """
    all_codes = [c for m in members for c in promo_codes_by_customer.get(m, []) if c]
    if not all_codes:
        return 0.0
    total = len(all_codes)
    counts: dict[str, int] = {}
    for c in all_codes:
        counts[c] = counts.get(c, 0) + 1
    herfindahl = sum((n / total) ** 2 for n in counts.values())

    total_orders = sum(len(promo_codes_by_customer.get(m, [])) for m in members) or 1
    adoption = total / total_orders
    return herfindahl * adoption


def _size_feature(size: int) -> float:
    """Log-scaled, NOT the main driver (see module docstring) -- scorer.py gives this
    the smallest weight of all six features. Included only because the spec asks for
    size as one input signal, e.g. a lone pair sharing a card is weaker evidence than
    a 6-person group doing the same thing."""
    return math.log1p(size)


def compute_community_features(
    graph: nx.Graph,
    communities: list[list[str]],
    data_dir: Path = DATA_DIR,
) -> list[CommunityFeatures]:
    """Compute features for every community. Deterministic: no sampling, no randomness."""
    customers = pd.read_csv(data_dir / "customers.csv", dtype=str)
    orders = pd.read_csv(data_dir / "orders.csv", dtype=str)
    events = pd.read_csv(data_dir / "events.csv", dtype=str)

    baseline = compute_global_baseline(customers, orders, events)

    created_at_by_customer = {
        row.customer_id: pd.Timestamp(row.created_at)
        for row in customers.itertuples(index=False)
    }

    orders_by_customer = orders.groupby("customer_id").size().to_dict()
    events_by_customer = events.groupby("customer_id").size().to_dict()

    promo_codes_by_customer: dict[str, list[str]] = {}
    for row in orders.itertuples(index=False):
        promo_codes_by_customer.setdefault(row.customer_id, []).append(
            row.promo_code if isinstance(row.promo_code, str) and row.promo_code else None
        )

    results = []
    for idx, members in enumerate(communities):
        high_weight_share = _high_weight_edge_share(graph, members)
        event_ratio, n_orders, n_bad_events, community_rate = _event_rate_ratio(
            members, orders_by_customer, events_by_customer, baseline
        )
        burst_score = _timing_burst_score(members, created_at_by_customer, baseline)
        fresh_ratio = _fresh_account_ratio(members, created_at_by_customer)
        promo_conc = _promo_concentration(members, promo_codes_by_customer)
        size_feat = _size_feature(len(members))

        results.append(
            CommunityFeatures(
                community_index=idx,
                size=len(members),
                members=members,
                high_weight_edge_share=high_weight_share,
                event_rate_ratio=event_ratio,
                timing_burst_score=burst_score,
                fresh_account_ratio=fresh_ratio,
                promo_concentration=promo_conc,
                size_feature=size_feat,
                n_orders=n_orders,
                n_bad_events=n_bad_events,
                community_event_rate=community_rate,
            )
        )
    return results
