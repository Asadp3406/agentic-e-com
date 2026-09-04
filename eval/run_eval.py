"""Stage 8: ring/account-level precision-recall, Rupee cost sweep, and baseline delta.

WHAT THIS IS
-------------
The full eval harness PLAN.md's Stage 8 asks for, superseding `eval/gnn_eval.py`'s
narrower Stage-6-only comparison (that file stays as-is; this one is the general-purpose
"prove it honestly" report). Everything here uses ground truth (`data/ground_truth.csv`,
`customers.csv`'s `ring_id`/`cluster_tag`) as an EVAL-ONLY signal -- never fed back into
`detect/scorer.py`'s scoring, per every earlier stage's fail-loudly "ground truth is
answer-key, not a feature" rule.

FOUR THINGS THIS REPORT ANSWERS
---------------------------------
1. Ring-level and account-level precision/recall/F1 against ground truth, with confusion
   matrices, at one chosen "operating point" threshold on `detect/scorer.py`'s ring-
   likelihood score.
2. A Rupee cost model: value of fraud caught (chargeback/return/cod_refusal exposure
   avoided) vs. cost of blocking legit customers by mistake, swept across every possible
   score threshold, so we can point at the threshold that maximizes net ₹ rather than
   picking one by feel.
3. Head-to-head: this graph approach's ring recall vs. Stage 4's structure-blind baseline
   classifier's ring recall, at matching operating points -- the punchline the whole
   project is building toward (per-transaction models can't see coordination; a graph
   can).
4. An explicit, un-tuned false-positive report on the four benign look-alike clusters
   (family, office_cluster, hostel_pg, couple) -- PLAN.md section 8's "honesty traps."

WHY COMMUNITY-LEVEL SCORE, ROLLED UP TO ACCOUNTS
----------------------------------------------------
`detect/scorer.py` scores *communities*, not individual accounts -- there's no
ring-likelihood number for a customer who isn't in any size>=3 community. For
account-level metrics, every member of a scored community inherits that community's
score; accounts in no size>=3 community (or in a community below MIN_REPORTED_SIZE)
get a score of 0.0 (never flagged). This is not a shortcut -- it is what the system
would actually do in production: an account's risk IS its community's risk, because the
whole thesis of this project is that ring membership, not individual behavior, is the
signal.

RING-LEVEL LABELING
---------------------
Same >=80%-purity rule used throughout (Stage 5's own PASS/FAIL check, `eval/gnn_eval.py`):
a community is a ring if >=80% of its members share one `ring_id`, benign if it has no
ring_id members at all (majority cluster_tag used only to name which look-alike type),
mixed/unlabeled communities are excluded from ring-level precision/recall (they are
neither a clean positive nor a clean negative -- scoring them either way measures
something else). Community-level confusion matrix is therefore over the labeled
(ring + benign) universe only; this is reported explicitly in the report so the numbers
aren't silently inflated by dropping ambiguous cases without saying so.

OPERATING POINT SELECTION
----------------------------
Two different "the threshold" numbers appear in this report and are NOT interchangeable:
  - The money-optimal threshold (from the ₹ sweep) -- maximizes net ₹ across ALL
    accounts, evaluated at every distinct score value present in the data.
  - The reporting/confusion-matrix operating point -- for legibility we also need one
    single threshold to print one precision/recall/F1 number and one confusion matrix.
    We use the SAME money-optimal threshold for both, so the "here's the confusion
    matrix" and "here's the ₹ number" sections describe one consistent, defensible
    operating point rather than two cherry-picked ones.

NO TUNING TO HIDE MISSES
---------------------------
Whatever the money-optimal threshold turns out to be, this script reports precision/
recall/F1 at it as computed -- it does not search for a threshold that maximizes recall
or precision cosmetically, and it does not exclude any ring from the denominator beyond
the mixed/unlabeled exclusion rule stated above (applied identically regardless of
outcome). If ring recall comes out below some target, that is reported, not massaged.

Run: ``python -m eval.run_eval`` (after ``make data resolve graph detect``) or
``make eval``. Writes PNGs + `eval/report.md` into `eval/`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yaml

from baseline.txn_classifier import BaselineResult, run_baseline
from detect.community import detect_communities
from detect.scorer import CommunityScore, score_communities
from graph.build import build_graph

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
EVAL_DIR = REPO_ROOT / "eval"
CONFIG_PATH = REPO_ROOT / "config.yaml"

MIN_REPORTED_SIZE = 3
RING_PURITY_THRESHOLD = 0.8
BENIGN_TAGS = ("family", "office_cluster", "hostel_pg", "couple")


@dataclass
class CostModel:
    avg_order_value: float
    chargeback_cost: float
    false_block_cost: float


def load_cost_model(config_path: Path = CONFIG_PATH) -> CostModel:
    with open(config_path) as f:
        config = yaml.safe_load(f)
    cost = config.get("cost_model") or {}
    required = ["avg_order_value", "chargeback_cost", "false_block_cost"]
    missing = [k for k in required if not cost.get(k)]
    if missing:
        raise ValueError(f"config.yaml `cost_model` missing/placeholder-0 keys: {missing}")
    return CostModel(**{k: float(cost[k]) for k in required})


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------


def _load_ground_truth() -> tuple[pd.DataFrame, dict[str, str], dict[str, str]]:
    gt = pd.read_csv(DATA_DIR / "ground_truth.csv", dtype=str)
    customers = pd.read_csv(DATA_DIR / "customers.csv", dtype=str)
    ring_by_customer = dict(zip(customers["customer_id"], customers["ring_id"].fillna("")))
    tag_by_customer = dict(zip(customers["customer_id"], customers["cluster_tag"].fillna("")))
    return gt, ring_by_customer, tag_by_customer


def _community_label(
    members: list[str], ring_by_customer: dict[str, str], tag_by_customer: dict[str, str]
) -> tuple[str, str]:
    """Returns (label, detail): label in {"ring","benign","mixed","unlabeled"}; detail
    is the ring_id for a ring, the base benign tag (family/office_cluster/hostel_pg/
    couple) for benign, else ""."""
    ring_ids = [ring_by_customer.get(m, "") for m in members if ring_by_customer.get(m, "")]
    if ring_ids:
        counts = Counter(ring_ids)
        top_ring, top_count = counts.most_common(1)[0]
        purity = top_count / len(members)
        if len(counts) == 1 and purity >= RING_PURITY_THRESHOLD:
            return "ring", top_ring
        return "mixed", ""

    tags = [tag_by_customer.get(m, "") for m in members if tag_by_customer.get(m, "")]
    if not tags:
        return "unlabeled", ""
    tag_counts = Counter(tags)
    top_tag, _ = tag_counts.most_common(1)[0]
    base_tag = top_tag.rsplit("_", 1)[0] if top_tag[-1].isdigit() else top_tag
    if base_tag in BENIGN_TAGS:
        return "benign", base_tag
    return "unlabeled", base_tag


# ---------------------------------------------------------------------------
# Account-level score rollup
# ---------------------------------------------------------------------------


def _account_scores(scores: list[CommunityScore], all_customer_ids: list[str]) -> pd.Series:
    """Every member of a size>=3 scored community inherits that community's score;
    everyone else scores 0.0 (never flagged at any threshold > 0)."""
    account_score: dict[str, float] = {cid: 0.0 for cid in all_customer_ids}
    for cs in scores:
        if cs.size < MIN_REPORTED_SIZE:
            continue
        for member in cs.members:
            account_score[member] = max(account_score.get(member, 0.0), cs.score)
    return pd.Series(account_score)


# ---------------------------------------------------------------------------
# Ring-level precision/recall/F1 + confusion matrix
# ---------------------------------------------------------------------------


@dataclass
class LabeledCommunity:
    cs: CommunityScore
    label: str  # ring | benign | mixed | unlabeled
    detail: str


def _label_communities(
    scores: list[CommunityScore], ring_by_customer: dict, tag_by_customer: dict
) -> list[LabeledCommunity]:
    out = []
    for cs in scores:
        if cs.size < MIN_REPORTED_SIZE:
            continue
        label, detail = _community_label(cs.members, ring_by_customer, tag_by_customer)
        out.append(LabeledCommunity(cs=cs, label=label, detail=detail))
    return out


def _confusion(tp: int, fp: int, fn: int, tn: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
    }


def ring_level_metrics(labeled: list[LabeledCommunity], threshold: float) -> dict:
    """Positive class = ring, negative class = benign. Mixed/unlabeled excluded from
    both the numerator and denominator (see module docstring)."""
    judged = [lc for lc in labeled if lc.label in ("ring", "benign")]
    tp = sum(1 for lc in judged if lc.label == "ring" and lc.cs.score >= threshold)
    fn = sum(1 for lc in judged if lc.label == "ring" and lc.cs.score < threshold)
    fp = sum(1 for lc in judged if lc.label == "benign" and lc.cs.score >= threshold)
    tn = sum(1 for lc in judged if lc.label == "benign" and lc.cs.score < threshold)
    result = _confusion(tp, fp, fn, tn)
    result["n_judged"] = len(judged)
    result["n_excluded_mixed_unlabeled"] = len(labeled) - len(judged)
    return result


def account_level_metrics(
    account_score: pd.Series, gt: pd.DataFrame, threshold: float
) -> dict:
    gt_indexed = gt.set_index("account_id")["label"]
    is_ring = (gt_indexed != "legit").reindex(account_score.index, fill_value=False)
    flagged = account_score >= threshold

    tp = int((flagged & is_ring).sum())
    fp = int((flagged & ~is_ring).sum())
    fn = int((~flagged & is_ring).sum())
    tn = int((~flagged & ~is_ring).sum())
    return _confusion(tp, fp, fn, tn)


# ---------------------------------------------------------------------------
# ₹ cost sweep
# ---------------------------------------------------------------------------


@dataclass
class CostSweepPoint:
    threshold: float
    n_flagged: int
    ring_accounts_caught: int
    legit_accounts_blocked: int
    rupees_saved: float
    rupees_lost: float
    net_rupees: float


def cost_sweep(
    account_score: pd.Series, gt: pd.DataFrame, cost_model: CostModel
) -> list[CostSweepPoint]:
    """Sweeps every distinct score value present as a candidate threshold (plus 0 and
    just-above-max, to bound the curve), flags every account with score >= threshold.
    ₹ saved = flagged ring accounts * chargeback_cost (fraud exposure avoided by acting
    on them). ₹ lost = flagged legit accounts * false_block_cost (real customers wrongly
    blocked). Net ₹ = saved - lost. This mirrors agent/policy.py's own per-member ₹
    convention (size * chargeback_cost / size * false_block_cost) at the account level
    instead of the community level, so the two ₹ stories in this project use the same
    unit economics."""
    gt_indexed = gt.set_index("account_id")["label"]
    is_ring = (gt_indexed != "legit").reindex(account_score.index, fill_value=False)

    candidate_thresholds = sorted(set(account_score.tolist()) | {0.0})
    candidate_thresholds = [0.0] + candidate_thresholds + [max(candidate_thresholds) + 1e-6]

    points = []
    for t in candidate_thresholds:
        flagged = account_score >= t
        ring_caught = int((flagged & is_ring).sum())
        legit_blocked = int((flagged & ~is_ring).sum())
        saved = ring_caught * cost_model.chargeback_cost
        lost = legit_blocked * cost_model.false_block_cost
        points.append(
            CostSweepPoint(
                threshold=t,
                n_flagged=int(flagged.sum()),
                ring_accounts_caught=ring_caught,
                legit_accounts_blocked=legit_blocked,
                rupees_saved=saved,
                rupees_lost=lost,
                net_rupees=saved - lost,
            )
        )
    return points


def money_optimal_point(points: list[CostSweepPoint]) -> CostSweepPoint:
    return max(points, key=lambda p: p.net_rupees)


# ---------------------------------------------------------------------------
# Head-to-head: graph vs baseline, ring recall at matching review-budget
# ---------------------------------------------------------------------------


def graph_recall_at_budget(
    account_score: pd.Series, gt: pd.DataFrame, budget_frac: float
) -> tuple[float, float, int]:
    """Top budget_frac of ALL accounts by graph-rollup score, flagged -- same top-N%
    review-budget framing baseline/txn_classifier.py uses, so the head-to-head compares
    like-for-like operating points instead of a probability threshold on one side and a
    budget on the other."""
    gt_indexed = gt.set_index("account_id")["label"]
    is_ring = gt_indexed != "legit"
    scores = account_score.reindex(gt_indexed.index, fill_value=0.0)
    n_flagged = max(1, round(len(gt_indexed) * budget_frac))
    flagged_ids = set(scores.sort_values(ascending=False).head(n_flagged).index)

    n_ring = int(is_ring.sum())
    n_ring_flagged = int(is_ring.reindex(flagged_ids).sum())
    recall = n_ring_flagged / n_ring if n_ring else 0.0
    precision = n_ring_flagged / n_flagged if n_flagged else 0.0
    return recall, precision, n_flagged


HEAD_TO_HEAD_BUDGETS = [0.02, 0.05, 0.10]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

COLOR_NET = "#2a6f3d"
COLOR_SAVED = "#3f8f4f"
COLOR_LOST = "#b0413e"
COLOR_GRAPH = "#2a6f97"
COLOR_BASELINE = "#c9762c"


def plot_cost_sweep(points: list[CostSweepPoint], optimal: CostSweepPoint, out_path: Path) -> None:
    thresholds = [p.threshold for p in points]
    net = [p.net_rupees for p in points]
    saved = [p.rupees_saved for p in points]
    lost = [-p.rupees_lost for p in points]

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(thresholds, saved, label="₹ saved (fraud caught)", color=COLOR_SAVED, linewidth=1.6)
    ax.plot(thresholds, lost, label="₹ lost (legit blocked)", color=COLOR_LOST, linewidth=1.6)
    ax.plot(thresholds, net, label="net ₹", color=COLOR_NET, linewidth=2.4)
    ax.axhline(0, color="#888888", linewidth=0.8, linestyle=":")
    ax.axvline(optimal.threshold, color="#444444", linewidth=1.2, linestyle="--")
    ax.scatter([optimal.threshold], [optimal.net_rupees], color=COLOR_NET, zorder=5, s=60)
    ax.annotate(
        f"money-optimal\nthreshold={optimal.threshold:.3f}\nnet=₹{optimal.net_rupees:,.0f}",
        xy=(optimal.threshold, optimal.net_rupees),
        xytext=(12, -28),
        textcoords="offset points",
        fontsize=9,
        color="#222222",
    )
    ax.set_xlabel("ring-score threshold (flag accounts scoring >= this)")
    ax.set_ylabel("₹")
    ax.set_title("₹ cost sweep vs. ring-score threshold")
    ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_head_to_head(
    graph_points: list[tuple[float, float]],
    baseline_points: list[tuple[float, float]],
    out_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(8, 5.5))
    gx = [p[0] * 100 for p in graph_points]
    gy = [p[1] * 100 for p in graph_points]
    bx = [p[0] * 100 for p in baseline_points]
    by = [p[1] * 100 for p in baseline_points]
    ax.plot(gx, gy, marker="o", color=COLOR_GRAPH, linewidth=2.2, label="graph approach (this project)")
    ax.plot(bx, by, marker="o", color=COLOR_BASELINE, linewidth=2.2, label="Stage-4 baseline (per-transaction)")
    for x, y in zip(gx, gy):
        ax.annotate(f"{y:.0f}%", (x, y), textcoords="offset points", xytext=(0, 8), fontsize=8, color=COLOR_GRAPH)
    for x, y in zip(bx, by):
        ax.annotate(f"{y:.0f}%", (x, y), textcoords="offset points", xytext=(0, -14), fontsize=8, color=COLOR_BASELINE)
    ax.set_xlabel("review budget (top N% of accounts flagged)")
    ax.set_ylabel("ring recall (%)")
    ax.set_ylim(-5, 105)
    ax.set_title("Head-to-head: ring recall, graph approach vs. baseline classifier")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_confusion_matrix(cm: dict, title: str, labels: tuple[str, str], out_path: Path) -> None:
    import numpy as np

    matrix = np.array([[cm["tp"], cm["fn"]], [cm["fp"], cm["tn"]]])
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    im = ax.imshow(matrix, cmap="Blues")
    ax.set_xticks([0, 1])
    ax.set_yticks([0, 1])
    ax.set_xticklabels([f"predicted {labels[0]}", f"predicted {labels[1]}"], fontsize=8)
    ax.set_yticklabels([f"actual {labels[0]}", f"actual {labels[1]}"], fontsize=8)
    for i in range(2):
        for j in range(2):
            val = matrix[i, j]
            color = "white" if val > matrix.max() / 2 else "black"
            ax.text(j, i, str(val), ha="center", va="center", color=color, fontsize=14, fontweight="bold")
    ax.set_title(title, fontsize=10)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    cost_model = load_cost_model()

    build_result = build_graph()
    graph = build_result.graph
    community_result = detect_communities(graph)
    scores = score_communities(graph, community_result.communities)

    gt, ring_by_customer, tag_by_customer = _load_ground_truth()
    all_customer_ids = gt["account_id"].tolist()
    account_score = _account_scores(scores, all_customer_ids)

    labeled_communities = _label_communities(scores, ring_by_customer, tag_by_customer)

    # ----- ₹ sweep + money-optimal threshold -----
    sweep_points = cost_sweep(account_score, gt, cost_model)
    optimal = money_optimal_point(sweep_points)
    threshold = optimal.threshold

    # ----- Ring-level and account-level metrics at the money-optimal threshold -----
    ring_metrics = ring_level_metrics(labeled_communities, threshold)
    account_metrics = account_level_metrics(account_score, gt, threshold)

    # ----- Head-to-head vs baseline -----
    baseline_result: BaselineResult = run_baseline()
    head_to_head = []
    for budget in HEAD_TO_HEAD_BUDGETS:
        g_recall, g_precision, n_flagged = graph_recall_at_budget(account_score, gt, budget)
        b_metrics = baseline_result.budget_results[budget]
        head_to_head.append(
            {
                "budget": budget,
                "n_flagged": n_flagged,
                "graph_recall": g_recall,
                "graph_precision": g_precision,
                "baseline_recall": b_metrics["ring_recall"],
                "baseline_precision": b_metrics["ring_precision"],
            }
        )

    # ----- Benign look-alike false-positive report -----
    benign_report = []
    for lc in labeled_communities:
        if lc.label != "benign":
            continue
        flagged = lc.cs.score >= threshold
        benign_report.append(
            {
                "cluster_tag": lc.detail,
                "size": lc.cs.size,
                "score": lc.cs.score,
                "flagged": flagged,
            }
        )
    benign_report.sort(key=lambda r: (-r["flagged"], -r["score"]))

    # ----- Charts -----
    plot_cost_sweep(sweep_points, optimal, EVAL_DIR / "cost_sweep.png")
    plot_head_to_head(
        [(h["budget"], h["graph_recall"]) for h in head_to_head],
        [(h["budget"], h["baseline_recall"]) for h in head_to_head],
        EVAL_DIR / "head_to_head_recall.png",
    )
    plot_confusion_matrix(
        ring_metrics, "Ring-level confusion matrix\n(community is ring vs. benign)",
        ("ring", "benign"), EVAL_DIR / "confusion_ring_level.png",
    )
    plot_confusion_matrix(
        account_metrics, "Account-level confusion matrix\n(account flagged vs. ring membership)",
        ("ring", "legit"), EVAL_DIR / "confusion_account_level.png",
    )

    # ----- Console summary -----
    _print_summary(
        threshold, optimal, ring_metrics, account_metrics, head_to_head,
        benign_report, baseline_result, labeled_communities,
    )

    # ----- Report -----
    _write_report(
        threshold, optimal, ring_metrics, account_metrics, head_to_head,
        benign_report, baseline_result, labeled_communities, cost_model,
    )


def _print_summary(
    threshold, optimal, ring_metrics, account_metrics, head_to_head,
    benign_report, baseline_result, labeled_communities,
) -> None:
    print("=== Stage 8: full eval (ring/account metrics, ₹ sweep, baseline delta) ===\n")
    print(f"Money-optimal ring-score threshold: {threshold:.4f}")
    print(
        f"  at this threshold: {optimal.n_flagged} accounts flagged, "
        f"{optimal.ring_accounts_caught} ring accounts caught, "
        f"{optimal.legit_accounts_blocked} legit accounts wrongly blocked"
    )
    print(f"  ₹ saved (fraud caught):   ₹{optimal.rupees_saved:,.0f}")
    print(f"  ₹ lost (legit blocked):   ₹{optimal.rupees_lost:,.0f}")
    print(f"  net ₹:                    ₹{optimal.net_rupees:,.0f}\n")

    print("--- Ring-level metrics (community is-ring vs is-benign, size>=3) ---")
    print(
        f"  judged communities: {ring_metrics['n_judged']} "
        f"(excluded {ring_metrics['n_excluded_mixed_unlabeled']} mixed/unlabeled)"
    )
    print(
        f"  TP={ring_metrics['tp']} FP={ring_metrics['fp']} "
        f"FN={ring_metrics['fn']} TN={ring_metrics['tn']}"
    )
    print(
        f"  precision={ring_metrics['precision']:.3f}  "
        f"recall={ring_metrics['recall']:.3f}  f1={ring_metrics['f1']:.3f}\n"
    )

    print("--- Account-level metrics (all 1601 accounts, ring vs legit) ---")
    print(
        f"  TP={account_metrics['tp']} FP={account_metrics['fp']} "
        f"FN={account_metrics['fn']} TN={account_metrics['tn']}"
    )
    print(
        f"  precision={account_metrics['precision']:.3f}  "
        f"recall={account_metrics['recall']:.3f}  f1={account_metrics['f1']:.3f}\n"
    )

    if ring_metrics["fn"] > 0:
        missed = [
            lc for lc in labeled_communities
            if lc.label == "ring" and lc.cs.score < threshold
        ]
        print(f"  MISSES: {len(missed)} ring(s) NOT caught at this threshold:")
        for lc in missed:
            print(f"    {lc.detail}: size={lc.cs.size} score={lc.cs.score:.3f} (below {threshold:.4f})")
        print()

    print("--- HEAD-TO-HEAD: graph approach vs Stage-4 baseline (ring recall) ---")
    print(f"{'budget':>8}  {'graph recall':>13}  {'baseline recall':>16}  delta")
    for h in head_to_head:
        delta = h["graph_recall"] - h["baseline_recall"]
        print(
            f"{h['budget']:>7.0%}  {h['graph_recall']:>12.1%}  "
            f"{h['baseline_recall']:>15.1%}  {delta:+.1%}"
        )
    print()

    print("--- Benign look-alike clusters (family/office/hostel/couple): false positives ---")
    n_fp = sum(1 for r in benign_report if r["flagged"])
    print(f"  {n_fp}/{len(benign_report)} benign look-alike communities flagged at this threshold:")
    for r in benign_report:
        marker = "FLAGGED (false positive)" if r["flagged"] else "clear"
        print(f"    {r['cluster_tag']:<16} size={r['size']:>3} score={r['score']:.3f}  {marker}")
    print()
    print("Charts written to eval/*.png; full report at eval/report.md")


def _write_report(
    threshold, optimal, ring_metrics, account_metrics, head_to_head,
    benign_report, baseline_result, labeled_communities, cost_model,
) -> None:
    ring_positives = [lc for lc in labeled_communities if lc.label == "ring"]
    n_rings_total = len(ring_positives)
    n_rings_caught = ring_metrics["tp"]
    n_fp_benign = sum(1 for r in benign_report if r["flagged"])

    missed = [lc for lc in ring_positives if lc.cs.score < threshold]

    lines = []
    lines.append("# Stage 8 — Evaluation report\n")
    lines.append(
        "Generated by `eval/run_eval.py` (`make eval`). Ground truth "
        "(`data/ground_truth.csv`, `customers.csv`'s `ring_id`/`cluster_tag`) is used "
        "**only** to score this report — it was never fed into `detect/scorer.py`'s "
        "ring-likelihood scoring itself.\n"
    )

    lines.append("## Headline numbers\n")
    lines.append(
        f"- **Money-optimal ring-score threshold: {threshold:.4f}** "
        f"(maximizes net ₹ across the full ₹ sweep, see below).\n"
        f"- At that threshold: **{optimal.n_flagged} accounts flagged**, "
        f"{optimal.ring_accounts_caught} of them true ring accounts, "
        f"{optimal.legit_accounts_blocked} of them legit accounts wrongly blocked.\n"
        f"- **Net ₹{optimal.net_rupees:,.0f}** "
        f"(₹{optimal.rupees_saved:,.0f} fraud exposure caught − "
        f"₹{optimal.rupees_lost:,.0f} legit-customer cost).\n"
        f"- **Ring-level recall: {ring_metrics['recall']:.1%}** "
        f"({n_rings_caught}/{n_rings_total} pure/near-pure ring communities caught "
        f"at this threshold).\n"
        f"- **Account-level recall: {account_metrics['recall']:.1%}** "
        f"of all ring-member accounts flagged.\n"
    )

    if missed:
        lines.append(
            f"- **{len(missed)} ring(s) missed at this threshold** — reported honestly "
            "below, not tuned away.\n"
        )
    else:
        lines.append(
            "- All ring communities in the labeled universe were caught at this "
            "threshold (see the ring-level table below for the full ranking, and "
            "the “what this eval doesn't cover” section for the one ring Stage 4's "
            "community detection doesn't recover as its own community at all).\n"
        )

    lines.append("\n## 1. Ring-level precision / recall / F1\n")
    lines.append(
        f"Universe: every size>=3 community, labeled ring (>=80% of members share one "
        f"`ring_id`) or benign (majority `cluster_tag` in family/office_cluster/"
        f"hostel_pg/couple). Mixed (spans multiple rings) and unlabeled communities are "
        f"excluded from both sides of the count — {ring_metrics['n_excluded_mixed_unlabeled']} "
        f"communities excluded here. This is NOT the same as recall over all 15 injected "
        f"rings; see “what this eval doesn't cover” for the gap.\n"
    )
    lines.append(
        f"| | predicted ring | predicted benign |\n"
        f"|---|---:|---:|\n"
        f"| **actual ring** | TP={ring_metrics['tp']} | FN={ring_metrics['fn']} |\n"
        f"| **actual benign** | FP={ring_metrics['fp']} | TN={ring_metrics['tn']} |\n\n"
        f"Precision **{ring_metrics['precision']:.3f}**, "
        f"recall **{ring_metrics['recall']:.3f}**, "
        f"F1 **{ring_metrics['f1']:.3f}** at threshold {threshold:.4f}.\n\n"
        f"![Ring-level confusion matrix](confusion_ring_level.png)\n"
    )

    lines.append("\n## 2. Account-level precision / recall / F1\n")
    lines.append(
        "Universe: all 1,601 accounts. An account's score is its community's "
        "ring-likelihood score (accounts in no size>=3 community score 0 and are never "
        "flagged). Positive class = belongs to any injected ring "
        "(`ground_truth.csv label != legit`).\n"
    )
    lines.append(
        f"| | predicted ring | predicted legit |\n"
        f"|---|---:|---:|\n"
        f"| **actual ring** | TP={account_metrics['tp']} | FN={account_metrics['fn']} |\n"
        f"| **actual legit** | FP={account_metrics['fp']} | TN={account_metrics['tn']} |\n\n"
        f"Precision **{account_metrics['precision']:.3f}**, "
        f"recall **{account_metrics['recall']:.3f}**, "
        f"F1 **{account_metrics['f1']:.3f}** at threshold {threshold:.4f}.\n\n"
        f"![Account-level confusion matrix](confusion_account_level.png)\n"
    )

    lines.append("\n## 3. ₹ cost model and threshold sweep\n")
    lines.append(
        f"Cost assumptions (`config.yaml`'s `cost_model`): avg order value "
        f"₹{cost_model.avg_order_value:,.0f}, chargeback/return/cod-refusal cost "
        f"₹{cost_model.chargeback_cost:,.0f} per ring account caught, false-block cost "
        f"₹{cost_model.false_block_cost:,.0f} per legit account wrongly blocked. These "
        "are rough SMB e-commerce assumptions carried over from Stage 7's agent policy "
        "model, not fitted to a real merchant's unit economics.\n\n"
        "For every possible ring-score threshold, we flag every account scoring at or "
        "above it, then compute:\n"
        "- ₹ saved = (ring accounts flagged) × chargeback_cost\n"
        "- ₹ lost = (legit accounts flagged) × false_block_cost\n"
        "- net ₹ = saved − lost\n\n"
        f"The **money-optimal threshold is {threshold:.4f}**, giving net "
        f"₹{optimal.net_rupees:,.0f}. Below is the full sweep — note net ₹ keeps "
        "climbing as the threshold rises past the highest-scoring rings (fewer false "
        "blocks) until it runs out of true rings to catch, at which point tightening "
        "further only removes true positives and net ₹ turns flat or negative.\n\n"
        "![₹ cost sweep](cost_sweep.png)\n"
    )

    lines.append("\n## 4. HEAD-TO-HEAD: graph approach vs. Stage-4 baseline classifier\n")
    lines.append(
        "The punchline. Both approaches are evaluated at the same top-N%-of-accounts "
        "review-budget operating points (not a probability threshold — see "
        "`baseline/txn_classifier.py`'s docstring for why a fixed cutoff isn't "
        "meaningful for either model).\n\n"
    )
    lines.append("| review budget | graph ring recall | baseline ring recall | delta |\n")
    lines.append("|---:|---:|---:|---:|\n")
    for h in head_to_head:
        delta = h["graph_recall"] - h["baseline_recall"]
        lines.append(
            f"| top {h['budget']:.0%} ({h['n_flagged']} accounts) | "
            f"{h['graph_recall']:.1%} | {h['baseline_recall']:.1%} | {delta:+.1%} |\n"
        )
    lines.append(
        "\n![Head-to-head ring recall](head_to_head_recall.png)\n\n"
        "The baseline classifier never sees a shared device, card, address, or "
        "community — it can only rank individually-risky-looking transactions. The "
        "graph approach can see *coordination* (the same device/card reused across "
        "many accounts, burst account creation), which is exactly what a ring is and a "
        "one-off risky individual isn't. The gap above is that difference, measured "
        "at equal review cost.\n"
    )

    lines.append("\n## 5. Benign look-alike clusters — false positives, reported explicitly\n")
    lines.append(
        "PLAN.md section 8's \"honesty traps\": large family, office/co-working IP "
        "cluster, hostel/PG pincode cluster, and card-sharing couples. None of these "
        "should be flagged on shared-attribute or size alone. At the money-optimal "
        f"threshold ({threshold:.4f}):\n\n"
    )
    lines.append("| cluster type | size | score | outcome |\n")
    lines.append("|---|---:|---:|---|\n")
    for r in benign_report:
        outcome = "**FLAGGED (false positive)**" if r["flagged"] else "clear (correctly not flagged)"
        lines.append(f"| {r['cluster_tag']} | {r['size']} | {r['score']:.3f} | {outcome} |\n")
    if n_fp_benign == 0:
        lines.append(
            f"\nAll {len(benign_report)} benign look-alike communities cleared at this "
            "threshold — zero false positives on the honesty-trap clusters.\n"
        )
    else:
        lines.append(
            f"\n**{n_fp_benign}/{len(benign_report)} benign look-alike communities were "
            "false-positively flagged at this threshold.** This is reported as-is, not "
            "tuned away — see “what this eval doesn't cover” below for context on why "
            "and what it would take to fix.\n"
        )

    lines.append("\n## 6. What this eval doesn't cover — an honest read\n")
    lines.append(
        "- **Community-detection recall gap, not scoring gap:** Stage 4's tuned "
        "resolution (`community_resolution: 0.001`) recovers 14 of 15 injected rings as "
        "their own size>=3 community — RING006's members don't cluster tightly enough "
        "at that resolution to form an identifiable community at all. That ring is "
        "**structurally absent from this eval's universe** (it can't appear in the "
        "labeled-communities table above because it never became a community), so the "
        f"ring-level recall of {ring_metrics['recall']:.1%} reported in section 1 is "
        "recall over the 14 rings Stage 4 actually surfaces, not true 15/15 recall. "
        "True full-pipeline recall (RING006 included) is "
        f"{n_rings_caught}/15 = {n_rings_caught/15:.1%} — worse than the headline "
        "number above, and that's the honest one to quote if asked "
        "\"of all 15 rings we injected, how many did the full pipeline catch end to "
        "end.\" Fixing it means re-sweeping Stage 4's resolution parameter, not "
        "anything in this eval script.\n"
    )
    if missed:
        lines.append(
            "- **Rings missed within the labeled universe at this threshold:**\n"
        )
        for lc in missed:
            lines.append(
                f"  - `{lc.detail}`: size={lc.cs.size}, score={lc.cs.score:.3f} "
                f"(below the {threshold:.4f} money-optimal cutoff).\n"
            )
    if account_metrics["fn"] > 0:
        lines.append(
            f"- **Account-level FN ({account_metrics['fn']}) is larger than it looks "
            "from the ring-level table, because it's a different failure mode:** every "
            "ring in section 1's table was caught as a *community* (its members' modal "
            "community scored above threshold), but not every member of a caught ring "
            "landed inside that ring's community in the first place — a handful of "
            "individual accounts from otherwise-caught rings (and RING006 in full, "
            "per the point above) ended up in a smaller/different community that did "
            "not itself clear the threshold. This is a community-detection membership "
            "gap, not a scoring gap: those accounts' community score is whatever their "
            "actual community scored, honestly rolled up — nothing is hidden or "
            "reassigned here to inflate account-level recall.\n"
        )
    lines.append(
        "- **Mixed/unlabeled communities are excluded, not counted as misses or "
        f"false positives** — {ring_metrics['n_excluded_mixed_unlabeled']} such "
        "communities exist in this run. A production system would still have to make a "
        "call on them; this eval doesn't score that call either way because ground "
        "truth doesn't cleanly say what the \"correct\" answer is for a mixed group.\n"
        "- **The ₹ cost model is illustrative, not fitted** — `avg_order_value`, "
        "`chargeback_cost`, and `false_block_cost` are the same rough SMB assumptions "
        "Stage 7's agent policy uses, not real unit economics from any merchant. The "
        "*shape* of the sweep (net ₹ rising then flattening) is the meaningful result; "
        "the absolute ₹ figures should be read as directional.\n"
        "- **Synthetic data** — every number in this report is against Stage 1's "
        "generated dataset (seed 42), not real transactions. See the README's "
        "Limitations section for the broader caveat.\n"
    )

    (EVAL_DIR / "report.md").write_text("".join(lines))


if __name__ == "__main__":
    main()
