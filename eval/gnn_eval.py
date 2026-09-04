"""Stage 6 eval: does the GNN anomaly feature improve ring recall over Stage 5 alone,
at the SAME false-positive rate?

WHY THIS EXISTS SEPARATELY FROM eval/run_eval.py
----------------------------------------------------
`eval/run_eval.py` is Stage 8's full metrics/₹-cost/baseline-delta harness and isn't
built yet (still a stub). Stage 6's exit test per PLAN.md is narrower and self-contained
("GNN improves ring recall at equal false-positive rate vs Stage 5 alone") and needs to
exist now, standalone, to decide `config.yaml`'s `gnn.enabled` flag -- so it lives here
rather than waiting on Stage 8. Stage 8 can absorb/call this later if useful.

METHODOLOGY
------------
Universe: every community Stage 4 returns with size >= 3 (matches scorer.py's own
MIN_REPORTED_SIZE reporting cutoff -- singletons/pairs aren't meaningful "review this
cluster" units).

Labels (ground truth used HERE ONLY, for eval -- never as a scoring input to either
variant): a community is a POSITIVE if it's a pure/near-pure ring (>=80% of members
share one ring_id, same rule scorer.py's `_ground_truth_tag` uses for its own PASS/FAIL
check) and a NEGATIVE if it's a benign look-alike (family/office/hostel/couple tag,
majority-vote). MIXED (multiple rings) and UNLABELED communities are excluded from both
the numerator and denominator of recall/FP-rate -- they're neither a clean ring nor a
clean benign example, so scoring them either way would be measuring something else.

Operating points: communities are ranked by score (highest first) and we sweep a
review-budget cutoff (top-N communities flagged as "suspicious") from 1 to
len(universe), exactly mirroring baseline/txn_classifier.py's and Stage 4/5's top-N%
framing rather than a probability threshold (same reasoning: this dataset's scores
don't have a meaningful fixed cutoff). At each N we compute:
  - ring recall    = (rings flagged) / (total rings)
  - FP rate        = (benign flagged) / (total benign)
For "same false-positive rate," we find, for each candidate FP-rate level actually hit
by the Stage-5-only ranking, the ring recall the Stage-5-only ranking achieves there,
and the ring recall the GNN-fused ranking achieves at the SAME FP rate (by finding the
smallest N in the GNN-fused ranking whose FP rate does not exceed that level) -- this
directly answers "at a review budget that lets through the same fraction of innocent
look-alikes, does adding the GNN feature let us catch more rings?"

Run: ``python -m eval.gnn_eval`` (after ``make data resolve graph``) or ``make eval``
(added to the Makefile). Trains the GAE once, computes both scored rankings, and prints
the before/after comparison table plus a verdict.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from detect.community import detect_communities
from detect.features import compute_community_features
from detect.gnn import GnnConfig, gnn_community_scores, load_gnn_config, train_gae
from detect.scorer import WEIGHTS, CommunityScore, score_community, weights_with_gnn
from graph.build import build_graph

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
MIN_REPORTED_SIZE = 3
RING_PURITY_THRESHOLD = 0.8


@dataclass
class LabeledScore:
    cs: CommunityScore
    label: str  # "ring" | "benign" | "mixed" | "unlabeled"


def _label(
    members: list[str], ring_by_customer: dict, tag_by_customer: dict
) -> str:
    ring_ids = [ring_by_customer.get(m, "") for m in members if ring_by_customer.get(m, "")]
    if ring_ids:
        counts = Counter(ring_ids)
        _, top_count = counts.most_common(1)[0]
        purity = top_count / len(members)
        if len(counts) == 1 and purity >= RING_PURITY_THRESHOLD:
            return "ring"
        return "mixed"
    tags = [tag_by_customer.get(m, "") for m in members if tag_by_customer.get(m, "")]
    if tags:
        return "benign"
    return "unlabeled"


def _score_and_label(
    graph, communities: list[list[str]], gnn_scores: dict[int, float] | None, weights: dict
) -> list[LabeledScore]:
    feature_list = compute_community_features(graph, communities, data_dir=DATA_DIR)
    scores = []
    for f in feature_list:
        gnn_score = gnn_scores[f.community_index] if gnn_scores is not None else None
        scores.append(score_community(f, gnn_score=gnn_score, weights=weights))
    scores.sort(key=lambda s: s.score, reverse=True)

    customers = pd.read_csv(DATA_DIR / "customers.csv", dtype=str)
    ring_by_customer = dict(zip(customers["customer_id"], customers["ring_id"].fillna("")))
    tag_by_customer = dict(zip(customers["customer_id"], customers["cluster_tag"].fillna("")))

    labeled = [
        LabeledScore(cs=cs, label=_label(cs.members, ring_by_customer, tag_by_customer))
        for cs in scores
        if cs.size >= MIN_REPORTED_SIZE
    ]
    return labeled


def _recall_fpr_curve(labeled: list[LabeledScore]) -> list[tuple[int, float, float]]:
    """Sweep top-N cutoffs over the (already score-sorted) labeled list. Returns
    [(N, ring_recall, benign_fp_rate), ...] for every N from 1 to len(labeled)."""
    total_rings = sum(1 for s in labeled if s.label == "ring")
    total_benign = sum(1 for s in labeled if s.label == "benign")

    curve = []
    rings_seen = 0
    benign_seen = 0
    for n, s in enumerate(labeled, start=1):
        if s.label == "ring":
            rings_seen += 1
        elif s.label == "benign":
            benign_seen += 1
        recall = rings_seen / total_rings if total_rings else 0.0
        fpr = benign_seen / total_benign if total_benign else 0.0
        curve.append((n, recall, fpr))
    return curve


def run_comparison(
    cfg: GnnConfig | None = None,
) -> dict:
    build_result = build_graph()
    graph = build_result.graph
    community_result = detect_communities(graph)
    communities = community_result.communities

    cfg = cfg or load_gnn_config()

    # Stage-5-only (baseline for this eval).
    stage5_labeled = _score_and_label(graph, communities, gnn_scores=None, weights=WEIGHTS)
    stage5_curve = _recall_fpr_curve(stage5_labeled)

    # Stage-5 + GNN-fused.
    train_result = train_gae(graph, cfg, data_dir=DATA_DIR)
    gnn_scores = gnn_community_scores(communities, train_result)
    fused_weights = weights_with_gnn(cfg.fusion_weight)
    fused_labeled = _score_and_label(graph, communities, gnn_scores=gnn_scores, weights=fused_weights)
    fused_curve = _recall_fpr_curve(fused_labeled)

    total_rings = sum(1 for s in stage5_labeled if s.label == "ring")
    total_benign = sum(1 for s in stage5_labeled if s.label == "benign")

    # Compare recall at each FP-rate level Stage-5-only actually hits (its own curve's
    # distinct fpr values), so the comparison always uses an operating point Stage 5
    # itself reaches, not an arbitrary round number.
    stage5_fpr_levels = sorted({fpr for _, _, fpr in stage5_curve})
    rows = []
    for target_fpr in stage5_fpr_levels:
        r5 = max((r for _, r, fpr in stage5_curve if fpr <= target_fpr + 1e-9), default=0.0)
        rfused = max((r for _, r, fpr in fused_curve if fpr <= target_fpr + 1e-9), default=0.0)
        rows.append((target_fpr, r5, rfused))

    return {
        "total_rings": total_rings,
        "total_benign": total_benign,
        "stage5_curve": stage5_curve,
        "fused_curve": fused_curve,
        "comparison_rows": rows,
        "gnn_final_loss": train_result.final_loss,
    }


def main() -> None:
    cfg = load_gnn_config()
    print("=== Stage 6 eval: GNN-fused vs Stage-5-only ring recall at equal FP rate ===")
    print(
        f"(config.yaml gnn.enabled={cfg.enabled} -- this eval always computes BOTH "
        "variants regardless of that flag, to decide what it should be set to)\n"
    )

    result = run_comparison(cfg)
    print(
        f"Universe: {result['total_rings']} pure/near-pure ring communities, "
        f"{result['total_benign']} benign look-alike communities (size >= {MIN_REPORTED_SIZE})"
    )
    print(f"GNN autoencoder final reconstruction loss: {result['gnn_final_loss']:.4f}\n")

    print(f"{'FP rate':>8}  {'Stage5-only recall':>19}  {'Stage5+GNN recall':>18}  delta")
    print("-" * 60)
    improved_anywhere = False
    worsened_anywhere = False
    printed_rows = []
    prev_r5, prev_rfused = None, None
    for target_fpr, r5, rfused in result["comparison_rows"]:
        delta = rfused - r5
        if delta > 1e-9:
            improved_anywhere = True
        elif delta < -1e-9:
            worsened_anywhere = True
        # Print every row where something changed from the previous one (recall isn't
        # constant), plus the very first row -- once both curves saturate at 100% the
        # remaining rows are redundant, so collapse them into one final summary line
        # instead of printing all ~180.
        if (r5, rfused) != (prev_r5, prev_rfused):
            printed_rows.append((target_fpr, r5, rfused, delta))
        prev_r5, prev_rfused = r5, rfused

    for target_fpr, r5, rfused, delta in printed_rows:
        marker = "  <-- better" if delta > 1e-9 else ("  <-- worse" if delta < -1e-9 else "")
        print(f"{target_fpr:>8.1%}  {r5:>19.1%}  {rfused:>18.1%}  {delta:+.1%}{marker}")
    n_collapsed = len(result["comparison_rows"]) - len(printed_rows)
    if n_collapsed > 0:
        print(f"  ... ({n_collapsed} further FP-rate levels omitted, both curves unchanged/saturated)")

    print()
    if improved_anywhere and not worsened_anywhere:
        print("VERDICT: GNN-fused strictly helps (recall >= Stage-5-only at every shared FP rate, "
              "better at at least one). Consider setting config.yaml gnn.enabled: true.")
    elif improved_anywhere and worsened_anywhere:
        print("VERDICT: MIXED -- GNN-fused helps at some FP-rate levels and hurts at others. "
              "Inspect the table above before enabling; not a clean win.")
    elif not improved_anywhere and not worsened_anywhere:
        print("VERDICT: NO CHANGE -- GNN-fused matches Stage-5-only everywhere in this sweep. "
              "No reason to pay the training cost; keep gnn.enabled: false.")
    else:
        print("VERDICT: GNN-fused HURTS ring recall at equal FP rate. Keep config.yaml "
              "gnn.enabled: false -- Stage 5 alone is better. See docs/stage-6-gnn.md.")


if __name__ == "__main__":
    main()
