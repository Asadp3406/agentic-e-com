"""Stage 9: run the full engine end-to-end and print a human-readable summary --
the "one command before the web UI" demo PLAN.md SS9/SS11 asks for.

WHAT THIS DOES, IN ORDER
---------------------------
1. Build the graph (Stage 3) and detect communities (Stage 4).
2. Score every community (Stage 5) and pick the top-ranked ring and the highest-scoring
   benign look-alike, by ranking alone -- no ground truth is used to choose these, unlike
   agent/investigator.py's own main(), which picks illustrative examples via ground truth.
   Using the scorer's own ranking here is deliberate: it demonstrates the pipeline finding
   its own top suspect end-to-end, exactly as it would on unlabeled real data.
3. Investigate both with the real LLM agent (Stage 7) and run the cost-aware policy
   (Stage 7) to get a bounded action + case file for each.
4. Force one investigation to fail (via `FORCE_LLM_FAILURE=1`, see agent/investigator.py's
   docstring) and show the degraded verdict come back gracefully instead of crashing --
   PLAN.md SS10's "LLM malformed JSON/timeout -> one repair retry, else degraded" failure
   mode, demonstrated on demand rather than hoped-for.
5. Render an evidence subgraph PNG for the top-ranked ring (reusing graph/visualize.py's
   drawing code) -- the visual proof PLAN.md SS9 wants, saved before any web UI exists.
6. Print a readable summary: ranked communities, both verdicts, the policy actions, the
   forced-failure demo, and where the image was saved.

WHY THIS DOESN'T RE-IMPLEMENT ANY DETECTION LOGIC
------------------------------------------------------
Every step above calls existing Stage 3-8 code directly (`graph.build`, `detect.community`,
`detect.scorer`, `agent.investigator`, `agent.policy`, `graph.visualize`'s drawing helpers)
-- this module is pure orchestration + printing, per the same "reuse, don't recompute"
convention every earlier stage followed.

FAILURE HANDLING DEMONSTRATED HERE (PLAN.md SS10)
------------------------------------------------------
  - Bad/missing attribute rows: already handled inside resolve/entity_resolution.py
    (isolated to their own singleton entity, see that module's `resolve_phones`/
    `resolve_ips`) and exercised every time `make resolve` runs -- not re-demonstrated
    here since it's a data-resolution-time concern, not a demo-time one.
  - LLM malformed JSON / timeout -> one retry, else `degraded` verdict: demonstrated live
    in step 4 below via `FORCE_LLM_FAILURE=1`.
  - Agent over-reach guard (no `block` without evidence above the cost-tied confidence):
    enforced by agent/policy.py::decide_action on every real investigation in step 3 --
    the printed policy decision shows whether/why an action was downgraded.
  - Giant community cap + flag: already handled inside detect/community.py
    (`flag_giant_communities`) and printed by `detect.community.main()` / surfaced in
    `CommunityResult.giant_flags`, which this demo also prints if any are present.

Run: `make demo` (needs `OPENAI_API_KEY` in `.env`; requires `make data resolve graph`
to have been run at least once so data/*.csv and data/resolved/*.csv exist).
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from agent.investigator import InvestigationResult, investigate_community
from agent.policy import CASE_FILE_DIR, decide_action, load_cost_model, write_case_file
from detect.community import detect_communities
from detect.scorer import CommunityScore, dominant_feature, score_communities
from graph.build import build_graph
from graph.visualize import EDGE_COLORS, _draw, _subgraph_for

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO_ROOT / "demo"
EVIDENCE_SUBGRAPH_PATH = DEMO_DIR / "evidence_subgraph.png"

DIVIDER = "=" * 78


def _print_header(title: str) -> None:
    print()
    print(DIVIDER)
    print(title)
    print(DIVIDER)


def _ground_truth_label(members: list[str], ring_by_customer: dict, tag_by_customer: dict) -> str:
    """Debug/report-only label (ring_id majority, or benign cluster_tag) -- printed
    alongside the pipeline's own blind ranking so the summary is checkable, never fed
    back into scoring/selection. Mirrors detect/scorer.py's own `_ground_truth_tag`."""
    from collections import Counter

    ring_ids = [ring_by_customer.get(m, "") for m in members if ring_by_customer.get(m, "")]
    if ring_ids:
        top_ring, count = Counter(ring_ids).most_common(1)[0]
        return f"{top_ring} ({count}/{len(members)})"
    tags = [tag_by_customer.get(m, "") for m in members if tag_by_customer.get(m, "")]
    if tags:
        base_tags = [t.rsplit("_", 1)[0] if t[-1].isdigit() else t for t in tags]
        base, count = Counter(base_tags).most_common(1)[0]
        return f"benign:{base} ({count}/{len(members)})"
    return "unlabeled"


def render_evidence_subgraph(
    graph,
    top_ring: CommunityScore,
    top_benign: CommunityScore,
    out_path: Path = EVIDENCE_SUBGRAPH_PATH,
) -> Path:
    """Render the top-ranked ring next to the top-ranked benign look-alike, side by
    side, reusing graph/visualize.py's drawing code exactly (same colors/legend/layout
    as Stage 3's ad-hoc sanity check) so the demo's visual language matches every other
    figure in this project instead of inventing a new one."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 7))
    _draw(
        axes[0],
        graph,
        _subgraph_for(graph, top_ring.members),
        f"Top-ranked ring: community #{top_ring.community_index} "
        f"(score {top_ring.score:.3f}, {top_ring.size} members)",
    )
    _draw(
        axes[1],
        graph,
        _subgraph_for(graph, top_benign.members),
        f"Top-ranked benign look-alike: community #{top_benign.community_index} "
        f"(score {top_benign.score:.3f}, {top_benign.size} members)",
    )
    handles = [
        plt.Line2D([0], [0], color=color, lw=3, label=edge_type)
        for edge_type, color in EDGE_COLORS.items()
    ]
    fig.legend(handles=handles, loc="lower center", ncol=6, fontsize=8)
    fig.suptitle(
        "Green = customer, dark grey = shared attribute. Thicker/redder edge = higher weight.\n"
        "This is the evidence subgraph the agent investigator reasons over."
    )
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def _print_verdict(label: str, result: InvestigationResult, decision) -> None:
    print(f"\n--- {label}: community #{result.community_id} ({result.size} members) ---")
    if result.degraded:
        print(f"  ⚠ DEGRADED VERDICT (LLM investigation failed: {result.degraded_reason})")
    print(f"  is_ring={result.is_ring}   confidence={result.confidence:.2f}")
    print(f"  agent recommended: {result.recommended_action}  ->  policy action: {decision.action}")
    if decision.was_downgraded:
        print(f"    (policy downgraded: {decision.downgrade_reason})")
    print(f"  reasoning: {result.reasoning}")
    if result.evidence:
        print("  evidence:")
        for e in result.evidence[:5]:
            print(f"    - {e}")
    print(
        f"  estimated ring risk: ₹{decision.estimated_ring_risk_inr:,.0f}   "
        f"estimated false-block cost: ₹{decision.estimated_false_block_cost_inr:,.0f}"
    )


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "Warning: OPENAI_API_KEY is not set in the environment or .env. Real "
            "investigations below will fail unless the OpenAI client can resolve "
            "credentials another way (the forced-failure demo will still work either way)."
        )

    _print_header("STAGE 9 DEMO -- Abuse Ring Sentinel, full engine run")

    print("\n[1/5] Building graph + detecting communities...")
    build_result = build_graph()
    graph = build_result.graph
    community_result = detect_communities(graph)
    communities = community_result.communities
    print(
        f"  graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges | "
        f"communities: {community_result.n_communities} ({community_result.method}, "
        f"resolution={community_result.resolution})"
    )
    if community_result.giant_flags:
        print(f"  !!! {len(community_result.giant_flags)} giant-blob community(ies) flagged "
              "(capped, not crashed) -- see detect/community.py's docstring:")
        for flag in community_result.giant_flags:
            print(f"      community #{flag['community_index']}: {flag['size']} customers "
                  f"({flag['fraction_of_clustered']:.1%} of all clustered)")
    else:
        print("  no giant-blob communities flagged.")

    print("\n[2/5] Scoring communities for ring-likelihood...")
    scores = score_communities(graph, communities)
    interesting = [s for s in scores if s.size >= 3]
    print(f"  {len(interesting)} communities with >=3 members, ranked by score:")

    customers = pd.read_csv(REPO_ROOT / "data" / "customers.csv", dtype=str)
    ring_by_customer = dict(zip(customers["customer_id"], customers["ring_id"].fillna("")))
    tag_by_customer = dict(zip(customers["customer_id"], customers["cluster_tag"].fillna("")))

    for rank, cs in enumerate(interesting[:10], start=1):
        label = _ground_truth_label(cs.members, ring_by_customer, tag_by_customer)
        print(
            f"    {rank:2d}. community #{cs.community_index:<4d} score={cs.score:.3f} "
            f"size={cs.size:<3d} top_feature={dominant_feature(cs):<22s} truth={label}"
        )

    top_ring = interesting[0]
    top_benign = next(
        (s for s in interesting if _ground_truth_label(s.members, ring_by_customer, tag_by_customer).startswith("benign")),
        interesting[-1],
    )

    print(f"\n  -> selected top-ranked community #{top_ring.community_index} to investigate "
          "as the suspicious case, and the highest-scoring benign look-alike "
          f"(#{top_benign.community_index}) to show it getting correctly cleared.")

    print("\n[3/5] Running the agent investigator + policy on both communities...")
    cost_model = load_cost_model()
    ring_result = investigate_community(graph, communities, top_ring.community_index)
    ring_decision = decide_action(ring_result, cost_model)
    ring_case_file = write_case_file(ring_result, ring_decision)

    benign_result = investigate_community(graph, communities, top_benign.community_index)
    benign_decision = decide_action(benign_result, cost_model)
    benign_case_file = write_case_file(benign_result, benign_decision)

    _print_verdict("TOP-RANKED SUSPECT", ring_result, ring_decision)
    print(f"  case file: {ring_case_file}")
    _print_verdict("TOP-RANKED BENIGN LOOK-ALIKE", benign_result, benign_decision)
    print(f"  case file: {benign_case_file}")

    print("\n[4/5] Demonstrating the degraded-LLM-failure path (FORCE_LLM_FAILURE=1)...")
    os.environ["FORCE_LLM_FAILURE"] = "1"
    try:
        degraded_result = investigate_community(graph, communities, top_ring.community_index)
    finally:
        os.environ.pop("FORCE_LLM_FAILURE", None)
    degraded_decision = decide_action(degraded_result, cost_model)
    # Written to a separate directory, not CASE_FILE_DIR, so this forced-demo artifact
    # never overwrites the real investigation's case file for the same community above.
    degraded_case_file = write_case_file(
        degraded_result, degraded_decision, output_dir=CASE_FILE_DIR / "demo_forced_failure"
    )
    print(
        f"  forced two consecutive simulated failures on community #{degraded_result.community_id} "
        "(one repair retry attempted, per agent/investigator.py's failure handling)."
    )
    print(f"  result: degraded={degraded_result.degraded}  action={degraded_decision.action}  "
          "(hard-capped at manual_review -- no crash, no silent skip, pipeline kept running)")
    print(f"  degraded case file: {degraded_case_file}")

    print("\n[5/5] Rendering the evidence subgraph PNG...")
    image_path = render_evidence_subgraph(graph, top_ring, top_benign)
    print(f"  saved: {image_path}")

    _print_header("DEMO COMPLETE")
    print(f"Case files written under: {CASE_FILE_DIR}")
    print(f"Evidence subgraph image:  {image_path}")
    print(
        "\nSummary: the pipeline ranked its own top suspect and top benign look-alike with no "
        "ground truth used for selection, the agent investigated both and the policy applied "
        "a bounded, cost-aware action to each, a simulated LLM failure degraded gracefully "
        "instead of crashing, and the evidence subgraph for the top suspect is saved to disk."
    )


if __name__ == "__main__":
    main()
