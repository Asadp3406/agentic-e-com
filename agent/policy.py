"""Stage 7: bounded action selection (monitor/hold/manual_review/block), gated by the
₹ cost model, plus the auditable per-community case file writer.

WHY THE AGENT DOESN'T GET THE FINAL WORD ON ACTION
------------------------------------------------------
`investigator.py`'s LLM proposes a `recommended_action` as part of its reasoning (it needs
to reason about proportionality to write a coherent case file), but an LLM's confidence
number is not the same thing as "safe to auto-block a real customer." This module is the
actual decision-maker: it recomputes the bounded action from (a) the agent's confidence,
(b) whether the agent actually cited evidence, and (c) the ₹ cost model in config.yaml, and
it can only ever DOWNGRADE what the agent proposed, never upgrade it — a policy layer that
could escalate an agent's own `monitor` into a `block` would defeat the point of having a
conservative gate at all.

THE COST-TIED CONFIDENCE RULE (TASK SPEC: "never recommend block below a confidence tied to
₹ risk")
------------------------------------------------------------------------------------------
`block` freezes/bans real accounts — if the verdict is wrong, that's `false_block_cost` (₹
1500 in config.yaml) times however many legitimate members are in the community. `hold`
(freeze payouts/shipping, pending review) is far less drastic — funds are held, not lost,
so a wrong call there costs friction, not ₹. The confidence bar for each action is set so
that the EXPECTED ₹ payoff of taking it is positive even under the agent's own stated
uncertainty:

    expected_value(action, confidence) =
        confidence * (₹ saved by stopping this ring) - (1 - confidence) * (₹ cost of being wrong)

For `block`: ₹ saved ≈ size * chargeback_cost (each member could have kept committing
chargebacks/returns/COD-refusals); ₹ cost of being wrong ≈ size * false_block_cost (every
member wrongly blocked). Requiring `confidence >= block_confidence_threshold` (0.85 in
config.yaml) keeps expected value positive even when false_block_cost and chargeback_cost
are the same order of magnitude (they are, by design — false positives are expensive
enough here that a moderate confidence isn't enough to justify blocking). `hold` uses a
lower bar (`hold_confidence_threshold`, 0.6) since its downside is bounded to friction, not
lost customers. Below the `hold` bar, everything downgrades further to `manual_review` — a
human makes the call, but the ring stays flagged and visible in the meantime (never
"nothing happens").

NO ACTION WITHOUT CITED EVIDENCE
------------------------------------
Per the task spec ("No action without cited evidence"), any verdict lacking a non-empty
`evidence` list — including a `degraded` verdict, which by construction has no real
evidence — is hard-capped at `manual_review` regardless of confidence or cost math. A
confident-sounding verdict with nothing backing it is exactly the failure mode a bounded-
action policy exists to prevent.

BOUNDED ACTIONS
-----------------
  monitor        — no friction; keep watching. Default for is_ring=False or low-confidence
                   ring calls.
  hold            — freeze payouts/shipping for this community's pending orders, pending
                   human review. Moderate confidence rings, or ambiguous "shares device but
                   only mildly elevated events, no timing burst" cases per PLAN.md SS8.
  manual_review   — route to a human fraud analyst with the full case file attached. Used
                   whenever confidence/evidence don't clear a higher bar, or the
                   investigation degraded.
  block           — freeze/ban the community's accounts outright. Only when confidence
                   clears `block_confidence_threshold` AND evidence is cited AND is_ring is
                   True.

CASE FILE
-----------
`write_case_file` persists one auditable JSON document per community under
`agent/case_files/community_<id>.json` — verdict, full evidence trail (every tool call and
its result, from `investigator.py`'s `ToolCallRecord`s), the policy's action + the reasoning
for any downgrade, and the ₹ estimates behind that reasoning. This is the artifact PLAN.md's
Stage 7 exit test and the demo (SS9) both point at.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from agent.investigator import InvestigationResult

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config.yaml"
CASE_FILE_DIR = REPO_ROOT / "agent" / "case_files"

BOUNDED_ACTIONS = ("monitor", "hold", "manual_review", "block")


@dataclass
class CostModel:
    avg_order_value: float
    chargeback_cost: float
    false_block_cost: float
    block_confidence_threshold: float
    hold_confidence_threshold: float


def load_cost_model(config_path: Path = CONFIG_PATH) -> CostModel:
    """Fails loudly on the `0` TODO placeholders, same fail-loudly policy as
    graph/weights.py::load_edge_weights() and detect/community.py::load_resolution() — a
    silent 0 here would make every ₹ estimate meaningless and let `block` sail past a
    threshold of 0 confidence."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    cost = config.get("cost_model")
    if cost is None:
        raise ValueError("config.yaml missing `cost_model`")

    required = [
        "avg_order_value",
        "chargeback_cost",
        "false_block_cost",
        "block_confidence_threshold",
        "hold_confidence_threshold",
    ]
    missing_or_zero = [k for k in required if not cost.get(k)]
    if missing_or_zero:
        raise ValueError(
            f"config.yaml `cost_model` has missing/placeholder-0 keys: {missing_or_zero} — "
            "set real values before running the agent (see agent/policy.py's module "
            "docstring for what each controls)."
        )
    return CostModel(**{k: float(cost[k]) for k in required})


@dataclass
class PolicyDecision:
    community_id: int
    size: int
    action: str
    agent_recommended_action: str
    was_downgraded: bool
    downgrade_reason: str | None
    estimated_ring_risk_inr: float
    estimated_false_block_cost_inr: float
    rationale: str


# Severity ranking so "downgrade" has a well-defined direction: hold and manual_review
# are treated as the same middle tier (neither is strictly more/less drastic than the
# other — hold acts on funds, manual_review acts on process), block is strictly the most
# severe, monitor strictly the least.
_SEVERITY = {"monitor": 0, "hold": 1, "manual_review": 1, "block": 2}


def decide_action(result: InvestigationResult, cost_model: CostModel) -> PolicyDecision:
    """Recompute the bounded action from the investigation result and the cost model, as
    a linear cascade of downgrade-only checks. Can only downgrade the agent's
    `recommended_action`, never upgrade it — see module docstring."""
    size = result.size
    estimated_ring_risk = size * cost_model.chargeback_cost
    estimated_false_block_cost = size * cost_model.false_block_cost

    proposed = result.recommended_action
    if proposed not in BOUNDED_ACTIONS:
        # Malformed action from the agent (shouldn't happen under structured outputs, but
        # this is exactly the kind of thing the failure-handling requirement covers) —
        # treat as the most conservative proposal rather than trusting an unknown string.
        proposed = "manual_review"

    final = proposed
    reason: str | None = None

    # 1. No action without cited evidence — hard cap at manual_review regardless of
    #    confidence or cost math. A degraded verdict has no real evidence by construction.
    if result.degraded or not result.evidence:
        if _SEVERITY[proposed] > _SEVERITY["manual_review"]:
            final = "manual_review"
            reason = (
                "investigation degraded (no LLM verdict available)"
                if result.degraded
                else "no cited evidence"
            )

    # 2. block requires confidence tied to ₹ risk. Below the bar, step down to hold (if
    #    hold's own bar is cleared) or all the way to manual_review.
    if final == "block" and result.confidence < cost_model.block_confidence_threshold:
        stepped_down = (
            "hold" if result.confidence >= cost_model.hold_confidence_threshold else "manual_review"
        )
        reason = (
            f"agent recommended block at confidence {result.confidence:.2f}, below the "
            f"₹-risk-tied block threshold ({cost_model.block_confidence_threshold:.2f}) — "
            f"a wrong block costs ~₹{estimated_false_block_cost:,.0f} across {size} members, "
            "too high to act on this confidence alone"
        )
        final = stepped_down

    # 3. hold has its own, lower confidence floor (bounded downside vs. block, but still
    #    not free — freezing a benign customer's order is real friction).
    if final == "hold" and result.confidence < cost_model.hold_confidence_threshold:
        final = "manual_review"
        reason = (
            f"confidence {result.confidence:.2f} is below the hold threshold "
            f"({cost_model.hold_confidence_threshold:.2f}) as well — routed to a human"
        )

    # 4. A benign verdict (is_ring=False) never justifies hold/block regardless of what the
    #    agent proposed — those actions exist to act against a ring, not a cleared cluster.
    if not result.is_ring and final in ("hold", "block"):
        final = "monitor"
        reason = "agent verdict was benign (is_ring=False); no escalation warranted"

    was_downgraded = final != proposed
    if reason is None and was_downgraded:
        reason = "policy adjusted the agent's proposed action to match evidence/confidence"

    if not was_downgraded:
        rationale = (
            f"Agent proposed '{proposed}' at confidence {result.confidence:.2f}; policy "
            f"confirmed it — evidence cited, confidence clears the relevant threshold, "
            f"estimated ring risk ~₹{estimated_ring_risk:,.0f} across {size} members."
        )
    else:
        rationale = (
            f"Agent proposed '{proposed}' at confidence {result.confidence:.2f}; policy "
            f"downgraded to '{final}': {reason}."
        )

    return PolicyDecision(
        community_id=result.community_id,
        size=size,
        action=final,
        agent_recommended_action=proposed,
        was_downgraded=was_downgraded,
        downgrade_reason=reason if was_downgraded else None,
        estimated_ring_risk_inr=round(estimated_ring_risk, 2),
        estimated_false_block_cost_inr=round(estimated_false_block_cost, 2),
        rationale=rationale,
    )


def write_case_file(
    result: InvestigationResult,
    decision: PolicyDecision,
    output_dir: Path = CASE_FILE_DIR,
) -> Path:
    """Write one auditable JSON case file per community. Includes the full tool-call
    evidence trail (not just the final verdict) so the case file is independently
    checkable — a reviewer can see exactly which graph facts the agent pulled and what they
    contained, not just trust the agent's summary of them."""
    output_dir.mkdir(parents=True, exist_ok=True)

    case_file = {
        "community_id": result.community_id,
        "size": result.size,
        "members": result.members,
        "verdict": {
            "is_ring": result.is_ring,
            "confidence": result.confidence,
            "evidence": result.evidence,
            "benign_explanations_considered": result.benign_explanations_considered,
            "reasoning": result.reasoning,
        },
        "degraded": result.degraded,
        "degraded_reason": result.degraded_reason,
        "tool_call_trail": [
            {"tool_name": tc.tool_name, "tool_input": tc.tool_input, "result": tc.result}
            for tc in result.tool_calls
        ],
        "policy_decision": asdict(decision),
    }

    path = output_dir / f"community_{result.community_id}.json"
    with open(path, "w") as f:
        json.dump(case_file, f, indent=2, default=str)
    return path


def investigate_and_decide(
    result: InvestigationResult,
    cost_model: CostModel | None = None,
    output_dir: Path = CASE_FILE_DIR,
) -> tuple[PolicyDecision, Path]:
    """Convenience wrapper: decide the action for an already-completed investigation and
    write its case file in one call."""
    cost_model = cost_model or load_cost_model()
    decision = decide_action(result, cost_model)
    path = write_case_file(result, decision, output_dir=output_dir)
    return decision, path


def main() -> None:
    """Run the Stage 7 exit test end-to-end: investigate a real ring and a benign family
    (agent/investigator.py's main() selection logic), decide bounded actions for both, and
    write their case files."""
    import pandas as pd

    from agent.investigator import investigate_community
    from detect.community import detect_communities
    from graph.build import build_graph

    build_result = build_graph()
    graph = build_result.graph
    community_result = detect_communities(graph)
    communities = community_result.communities

    customers = pd.read_csv(REPO_ROOT / "data" / "customers.csv", dtype=str)
    ring_by_customer = dict(zip(customers["customer_id"], customers["ring_id"].fillna("")))
    tag_by_customer = dict(zip(customers["customer_id"], customers["cluster_tag"].fillna("")))

    def ring_purity(members: list[str]) -> float:
        from collections import Counter

        ring_ids = [ring_by_customer.get(m, "") for m in members if ring_by_customer.get(m, "")]
        if not ring_ids:
            return 0.0
        counts = Counter(ring_ids)
        _, top_count = counts.most_common(1)[0]
        return top_count / len(members)

    def benign_base_tag(members: list[str]) -> str:
        tags = [tag_by_customer.get(m, "") for m in members]
        counts: dict[str, int] = {}
        for t in tags:
            if t:
                base = t.rsplit("_", 1)[0] if t[-1].isdigit() else t
                counts[base] = counts.get(base, 0) + 1
        return max(counts, key=counts.get) if counts else ""

    ring_id = next(
        idx for idx, m in enumerate(communities) if len(m) >= 3 and ring_purity(m) >= 0.8
    )
    benign_id = next(
        idx
        for idx, m in enumerate(communities)
        if len(m) >= 3 and ring_purity(m) == 0.0 and benign_base_tag(m) == "family"
    )

    cost_model = load_cost_model()
    print("=== Stage 7: agent + policy exit test ===")
    print(
        f"cost model: avg_order_value=₹{cost_model.avg_order_value:.0f}  "
        f"chargeback_cost=₹{cost_model.chargeback_cost:.0f}  "
        f"false_block_cost=₹{cost_model.false_block_cost:.0f}  "
        f"block>={cost_model.block_confidence_threshold}  hold>={cost_model.hold_confidence_threshold}"
    )

    for label, cid in [("RING", ring_id), ("BENIGN FAMILY", benign_id)]:
        print(f"\n--- {label}: community #{cid} ({len(communities[cid])} members) ---")
        result = investigate_community(graph, communities, cid)
        decision, path = investigate_and_decide(result, cost_model=cost_model)
        print(f"is_ring={result.is_ring}  confidence={result.confidence:.2f}")
        print(f"agent recommended: {result.recommended_action}  -> policy action: {decision.action}")
        if decision.was_downgraded:
            print(f"  (downgraded: {decision.downgrade_reason})")
        print(f"case file: {path}")


if __name__ == "__main__":
    main()
