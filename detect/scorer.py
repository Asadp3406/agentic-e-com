"""Stage 5: combine per-community features into a ring-likelihood score and rank communities.

SCORING APPROACH
-----------------
A weighted sum of squashed [0,1] sub-scores, not a learned model -- there's no labeled
training signal we're allowed to use here without cheating (ground truth exists only
for eval, per graph/build.py's docstring: `ring_id`/`cluster_tag` "are ground-truth/
debug labels only, not signals scoring should use"). Each raw feature from
`features.py` is squashed into [0,1] with a feature-appropriate curve, then combined
with fixed weights that reflect how strong/reliable each signal is *by construction* of
the Stage 1 generator (see features.py's module docstring for the full reasoning on
why each feature was chosen):

  event_rate_ratio          0.35  -- the strongest behavioral signal (0.04 baseline vs
                                     0.35-0.65 ring rate is an 9-16x gap by design)
  high_weight_edge_share    0.25  -- rings deliberately reuse device/card; this is a
                                     share of evidence, not a raw count, so it isn't
                                     fooled by a big low-weight benign cluster
  timing_burst_score        0.20  -- rings are burst-created (1-10 days); benign
                                     clusters are spread over the ~1yr window
  fresh_account_ratio       0.10  -- a softer/different cut of the same burst signal,
                                     lower weight to avoid double-counting burstiness
  promo_concentration       0.07  -- only ~50% of rings farm a promo code, so this is
                                     corroborating evidence, not load-bearing on its own
  size (log-scaled)         0.03  -- explicitly minor per the spec ("NOT the main
                                     driver") -- included so a 2-person high-evidence
                                     pair doesn't score identically to a 9-person one,
                                     without letting community size dominate

Weights sum to 1.0, so the combined score is already in [0,1] when every sub-score is.

WHY NOT JUST THRESHOLD event_rate_ratio ALONE
------------------------------------------------
It's tempting to say "elevated chargebacks == fraud" and stop there, but that's
exactly the baseline classifier's blind spot (Stage 4's `baseline/txn_classifier.py`
already does this at the transaction level and gets 26-59% recall depending on
budget). A community of totally unrelated customers who each independently had one bad
order would show an elevated rate too, without being a coordinated ring -- that's why
high_weight_edge_share (deliberate attribute reuse) and timing_burst_score (coordinated
arrival) carry almost as much combined weight: a ring needs BOTH elevated risk AND
structural/temporal coordination, not just one or the other.

CALIBRATION LOOP (the actual point of Stage 5)
------------------------------------------------
`main()` below runs the full pipeline and prints every community with size >= 3,
ranked by score, with a ground-truth tag (ring / benign-tag / mixed / unlabeled) next
to each -- ground truth is used HERE ONLY, for reporting how the blind score did, never
as a scoring input. If a benign look-alike (family/office/hostel/couple) had ranked
high, the fix belongs in features.py/scorer.py's weighting, not in ground-truth
lookup -- see docs/stage-5-ring-scoring.md for what was actually found while tuning
this.

OPTIONAL 7TH FEATURE: gnn_anomaly (Stage 6)
----------------------------------------------
When `config.yaml`'s `gnn.enabled` is true, a 7th sub-score `gnn_anomaly` is added --
the per-community-aggregated, min-max-normalized reconstruction-error signal from
`detect/gnn.py`'s graph autoencoder (see that module's docstring for the full approach).
It's folded in at `gnn.fusion_weight` from config.yaml, with the original six weights
above scaled down proportionally so everything still sums to 1.0 -- this preserves their
*relative* ordering (event_rate_ratio is still the single strongest hand-crafted signal)
rather than picking new weights by hand a second time. When `gnn.enabled` is false
(the default -- see docs/stage-6-gnn.md for why), `score_communities()` skips GNN
training entirely and behaves exactly as it did before Stage 6 existed: six features,
weights as listed above, unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from detect.community import CommunityResult, detect_communities
from detect.features import CommunityFeatures, compute_community_features
from graph.build import build_graph

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

BASE_WEIGHTS = {
    "event_rate_ratio": 0.35,
    "high_weight_edge_share": 0.25,
    "timing_burst_score": 0.20,
    "fresh_account_ratio": 0.10,
    "promo_concentration": 0.07,
    "size": 0.03,
}

# Kept as the module-level default (matches pre-Stage-6 behavior / no GNN feature) --
# score_community()/score_communities() compute the GNN-fused variant on demand via
# weights_with_gnn() instead of mutating this.
WEIGHTS = dict(BASE_WEIGHTS)


def weights_with_gnn(fusion_weight: float) -> dict[str, float]:
    """Scales the six hand-crafted weights down proportionally to make room for a
    gnn_anomaly weight of `fusion_weight`, preserving their relative ordering rather
    than picking new weights by hand a second time. Still sums to 1.0."""
    scale = 1.0 - fusion_weight
    scaled = {k: v * scale for k, v in BASE_WEIGHTS.items()}
    scaled["gnn_anomaly"] = fusion_weight
    return scaled

# event_rate_ratio is unbounded above (a community could in principle be 100% of its
# orders bad, ratio ~25x baseline) -- squash it with a saturating curve so one extreme
# outlier community doesn't need special-casing, and so "2x baseline" and "20x
# baseline" are both clearly "high" without the raw ratio blowing past 1.0. Ratio 1.0
# (exactly baseline) squashes to ~0.5; ratio 3.0+ (3x baseline, well within the
# generator's 9-16x ring range) squashes to ~0.9+.
EVENT_RATIO_SATURATION = 1.5

# log1p(size) needs its own squash to land in [0,1]; the largest realistic community in
# this dataset is the ~40-person hostel, so normalize against log1p(50) as a practical
# ceiling rather than the dataset's literal max (which would make the single largest
# community always score exactly 1.0 on size regardless of what it actually is).
SIZE_LOG_CEILING = 50


def _squash_ratio(ratio: float, saturation: float = EVENT_RATIO_SATURATION) -> float:
    """Maps [0, inf) -> [0, 1) via ratio / (ratio + saturation), a simple saturating
    curve. ratio=0 -> 0.0, ratio=saturation -> 0.5, ratio=inf -> 1.0."""
    if ratio <= 0:
        return 0.0
    return ratio / (ratio + saturation)


def _squash_size(size_feature: float) -> float:
    import math

    ceiling = math.log1p(SIZE_LOG_CEILING)
    return min(size_feature / ceiling, 1.0)


@dataclass
class CommunityScore:
    community_index: int
    size: int
    members: list[str]
    score: float
    sub_scores: dict[str, float]
    features: CommunityFeatures


def score_community(
    features: CommunityFeatures,
    gnn_score: float | None = None,
    weights: dict[str, float] | None = None,
) -> CommunityScore:
    """`gnn_score` (already min-max normalized into [0,1] by
    `detect.gnn.gnn_community_scores`) is optional -- pass it (and a matching `weights`
    from `weights_with_gnn()`) to fuse Stage 6's signal in; omit both for Stage-5-only
    behavior, unchanged from before Stage 6 existed."""
    weights = weights or WEIGHTS
    sub_scores = {
        "event_rate_ratio": _squash_ratio(features.event_rate_ratio),
        "high_weight_edge_share": max(0.0, min(features.high_weight_edge_share, 1.0)),
        "timing_burst_score": max(0.0, min(features.timing_burst_score, 1.0)),
        "fresh_account_ratio": max(0.0, min(features.fresh_account_ratio, 1.0)),
        "promo_concentration": max(0.0, min(features.promo_concentration, 1.0)),
        "size": _squash_size(features.size_feature),
    }
    if gnn_score is not None:
        sub_scores["gnn_anomaly"] = max(0.0, min(gnn_score, 1.0))
    score = sum(weights[k] * v for k, v in sub_scores.items())
    return CommunityScore(
        community_index=features.community_index,
        size=features.size,
        members=features.members,
        score=score,
        sub_scores=sub_scores,
        features=features,
    )


def score_communities(
    graph,
    communities: list[list[str]],
    data_dir: Path = DATA_DIR,
    use_gnn: bool | None = None,
) -> list[CommunityScore]:
    """Compute features + scores for every community, ranked highest-score first.

    `use_gnn`: None (default) reads `config.yaml`'s `gnn.enabled`; pass True/False to
    override (used by eval/gnn_eval.py to compute both variants in one process without
    editing config.yaml). When on, trains detect.gnn's autoencoder fresh each call --
    see that module's docstring; this is a deliberate simplicity-over-speed choice for
    a project this size, not meant to be called in a hot loop.
    """
    feature_list = compute_community_features(graph, communities, data_dir=data_dir)

    from detect.gnn import gnn_community_scores, load_gnn_config, train_gae

    cfg = load_gnn_config()
    enabled = cfg.enabled if use_gnn is None else use_gnn

    if not enabled:
        scores = [score_community(f) for f in feature_list]
    else:
        train_result = train_gae(graph, cfg, data_dir=data_dir)
        gnn_scores = gnn_community_scores(communities, train_result)
        weights = weights_with_gnn(cfg.fusion_weight)
        scores = [
            score_community(f, gnn_score=gnn_scores[f.community_index], weights=weights)
            for f in feature_list
        ]

    scores.sort(key=lambda s: s.score, reverse=True)
    return scores


def dominant_feature(cs: CommunityScore, weights: dict[str, float] | None = None) -> str:
    """Which weighted sub-score contributed the most to this community's score --
    used in the report so a surprising ranking (e.g. a benign cluster scoring high)
    can be traced back to the responsible feature at a glance. Pass the same `weights`
    used to compute `cs` (defaults to the base six-feature WEIGHTS) so this works for
    both the Stage-5-only and GNN-fused score variants."""
    weights = weights or WEIGHTS
    contributions = {k: weights[k] * v for k, v in cs.sub_scores.items()}
    return max(contributions, key=contributions.get)


# ---------------------------------------------------------------------------
# Reporting (ground truth used here ONLY, for diagnostics -- never as a scoring input)
# ---------------------------------------------------------------------------


def _ground_truth_tag(members: list[str], ring_by_customer: dict, tag_by_customer: dict) -> str:
    ring_ids = [ring_by_customer.get(m, "") for m in members if ring_by_customer.get(m, "")]
    tags = [tag_by_customer.get(m, "") for m in members]

    if ring_ids:
        from collections import Counter

        counts = Counter(ring_ids)
        top_ring, top_count = counts.most_common(1)[0]
        purity = top_count / len(members)
        if len(counts) == 1 and purity >= 0.8:
            return f"RING:{top_ring} ({purity:.0%} pure)"
        return f"MIXED (rings: {dict(counts)}, purity {purity:.0%})"

    tag_counts: dict[str, int] = {}
    for t in tags:
        if t:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    if tag_counts:
        top_tag = max(tag_counts, key=tag_counts.get)
        base_tag = top_tag.rsplit("_", 1)[0] if top_tag[-1].isdigit() else top_tag
        return f"benign:{base_tag}"
    return "unlabeled"


MIN_REPORTED_SIZE = 3


def main() -> None:
    from detect.gnn import load_gnn_config

    build_result = build_graph()
    graph = build_result.graph

    community_result: CommunityResult = detect_communities(graph)
    gnn_cfg = load_gnn_config()
    scores = score_communities(graph, community_result.communities)
    weights = weights_with_gnn(gnn_cfg.fusion_weight) if gnn_cfg.enabled else WEIGHTS

    customers = pd.read_csv(DATA_DIR / "customers.csv", dtype=str)
    ring_by_customer = dict(zip(customers["customer_id"], customers["ring_id"].fillna("")))
    tag_by_customer = dict(zip(customers["customer_id"], customers["cluster_tag"].fillna("")))

    print("=== Stage 5: ring-likelihood scoring ===")
    print(f"Communities scored: {len(scores)} (from {community_result.method} detection)")
    print(f"GNN anomaly feature (Stage 6): {'ON' if gnn_cfg.enabled else 'OFF'} (config.yaml gnn.enabled)")

    reportable = [s for s in scores if s.size >= MIN_REPORTED_SIZE]
    print(
        f"\nShowing {len(reportable)} communities with size >= {MIN_REPORTED_SIZE} "
        f"(of {len(scores)} total), ranked by ring-likelihood score:\n"
    )

    gnn_col = f" {'gnn':>5}" if gnn_cfg.enabled else ""
    header = (
        f"{'rank':>4} {'score':>6} {'size':>5}  {'evt':>5} {'edge':>5} {'burst':>5} "
        f"{'fresh':>5} {'promo':>5} {'sz':>5}{gnn_col}  {'ground truth':<28} top-feature"
    )
    print(header)
    print("-" * len(header))
    for rank, cs in enumerate(reportable, start=1):
        gt = _ground_truth_tag(cs.members, ring_by_customer, tag_by_customer)
        top = dominant_feature(cs, weights)
        ss = cs.sub_scores
        gnn_val = f" {ss['gnn_anomaly']:>5.2f}" if gnn_cfg.enabled else ""
        print(
            f"{rank:>4} {cs.score:>6.3f} {cs.size:>5}  "
            f"{ss['event_rate_ratio']:>5.2f} {ss['high_weight_edge_share']:>5.2f} "
            f"{ss['timing_burst_score']:>5.2f} {ss['fresh_account_ratio']:>5.2f} "
            f"{ss['promo_concentration']:>5.2f} {ss['size']:>5.2f}{gnn_val}  "
            f"{gt:<28} {top}"
        )

    # Exit-test summary: where did injected rings land vs benign look-alikes?
    ring_communities = [
        (rank, cs)
        for rank, cs in enumerate(reportable, start=1)
        if _ground_truth_tag(cs.members, ring_by_customer, tag_by_customer).startswith("RING:")
    ]
    benign_communities = [
        (rank, cs)
        for rank, cs in enumerate(reportable, start=1)
        if _ground_truth_tag(cs.members, ring_by_customer, tag_by_customer).startswith("benign:")
    ]

    print(f"\n=== Exit test: rings vs benign look-alikes ===")
    print(f"Pure/near-pure ring communities found: {len(ring_communities)}")
    for rank, cs in ring_communities:
        print(f"  rank {rank:>3}/{len(reportable)}  score={cs.score:.3f}  size={cs.size}")

    print(f"\nBenign look-alike communities (size >= {MIN_REPORTED_SIZE}): {len(benign_communities)}")
    for rank, cs in benign_communities:
        print(f"  rank {rank:>3}/{len(reportable)}  score={cs.score:.3f}  size={cs.size}")

    if ring_communities and benign_communities:
        worst_ring_rank = max(r for r, _ in ring_communities)
        best_benign_rank = min(r for r, _ in benign_communities)
        if worst_ring_rank < best_benign_rank:
            print(
                f"\nPASS: every ring (worst rank {worst_ring_rank}) outranks every "
                f"benign look-alike (best rank {best_benign_rank})."
            )
        else:
            print(
                f"\nFAIL: at least one benign look-alike (best rank {best_benign_rank}) "
                f"outranks at least one ring (worst rank {worst_ring_rank}) -- "
                "inspect the offending community's top-feature column above."
            )


if __name__ == "__main__":
    main()
