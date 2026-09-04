"""Stage 3: compute edge weights by discriminativeness (rarity) of each shared attribute type.

WHY WEIGHT BY DISCRIMINATIVENESS
---------------------------------
Two customers sharing an attribute is only evidence of coordination if that attribute is
*rare by accident*. Sharing a device fingerprint or a payment card is very unlikely to
happen between strangers -- it takes deliberate reuse. Sharing a /24 IP subnet or a postal
pincode is extremely likely to happen between total strangers (a whole ISP region or a
whole neighborhood shares those), so it's weak evidence on its own.

If every shared attribute contributed the same weight, community detection would be
swamped by high-degree, low-information nodes (a busy pincode or ISP subnet touches
thousands of unrelated customers) and would merge unrelated legit clusters into giant
blobs, or dilute real rings' signal under noise. Weighting edges by rarity lets the
graph's structure do the work: a ring lights up because its members are tied together by
*several independent high-weight* shares (same device AND same card), not because they
happen to live in the same city.

THE SCALE (see config.yaml `edge_weights`, values duplicated here as the source of truth)
-------------------------------------------------------------------------------------------
  device     1.0   Highest. A device fingerprint match across two "different" customers
                    is essentially never accidental -- it means the same physical phone/
                    browser was used to create or operate both accounts.
  card       0.9    Near-highest. A shared card/UPI VPA means the same payment instrument
                    funded both accounts. Slightly below device because legitimate
                    instrument-sharing exists (couples, family cards -- see Stage 1's
                    "honesty trap" clusters) but it's still a strong, deliberate link.
  phone      0.7    Medium-high. A shared normalized phone number is a real, hard-to-fake
                    link, but phone reassignment/shared household lines make it slightly
                    less certain than device/card.
  address    0.6    Medium. Stage 2 fuzzy-matches near-duplicate address text (blocked by
                    city+pincode, gated on unit number -- see
                    docs/stage-2-entity-resolution.md), so a match here means "same
                    physical residence," which legit families/couples/hostel-mates share
                    innocently as often as rings do deliberately.
  ip_subnet  0.2    Low. A shared /24 IP subnet covers a whole ISP allocation block or
                    office/campus NAT -- thousands of unrelated people can share one.
  pincode    0.1    Lowest. A shared postal pincode covers a whole neighborhood --
                    the weakest possible signal, kept mainly so the agent/GNN stages have
                    a "background noise" edge type to contrast real signal against.

These are relative, hand-set priors (not learned from data) -- the point isn't the exact
number, it's device/card >> phone > address >> ip_subnet > pincode. They live in
config.yaml so they can be tuned without touching code; this module just reads them and
is the single place that maps an attribute-type name to its weight.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"

# Edge types the graph builder knows about, in descending discriminativeness order.
EDGE_TYPES = ["device", "card", "phone", "address", "ip_subnet", "pincode"]


def load_edge_weights(config_path: Path = CONFIG_PATH) -> dict[str, float]:
    """Load the `edge_weights` block from config.yaml.

    Fails loudly (rather than silently defaulting) if a weight is missing or still at
    the zero TODO placeholder, so a mis-configured run can't produce a silently-flat,
    meaningless graph.
    """
    with open(config_path) as f:
        config = yaml.safe_load(f)

    weights = config.get("edge_weights") or {}
    missing = [t for t in EDGE_TYPES if t not in weights]
    if missing:
        raise ValueError(f"config.yaml edge_weights missing entries for: {missing}")

    zeroed = [t for t in EDGE_TYPES if weights[t] == 0]
    if zeroed:
        raise ValueError(
            f"config.yaml edge_weights still at placeholder 0 for: {zeroed} "
            "-- fill in real values before building the graph."
        )

    return {t: float(weights[t]) for t in EDGE_TYPES}


def weight_for(edge_type: str, weights: dict[str, float] | None = None) -> float:
    """Return the configured weight for a single edge type."""
    weights = weights or load_edge_weights()
    if edge_type not in weights:
        raise KeyError(f"unknown edge type: {edge_type!r}")
    return weights[edge_type]
