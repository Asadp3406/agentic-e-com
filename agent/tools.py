"""Stage 7: deterministic graph-query tools the LLM agent calls to gather evidence.

WHY TOOLS AND NOT A DUMP OF EVERYTHING IN ONE PROMPT
------------------------------------------------------
PLAN.md is explicit that Stage 7 must be a "real tool-use agent, not a one-shot prompt."
The point isn't ceremony -- it's that the agent has to *decide what to look at next* based
on what it already learned, the same way a human fraud analyst would (see the members and
shared entities first, then decide whether timing/events/baseline comparison actually
supports "ring" or points to "benign"). A single mega-prompt with every number already
computed would let the LLM pattern-match on a wall of text; a tool-use loop forces it to
ask for evidence and reason about each answer before asking for more.

EVERY NUMBER HERE IS COMPUTED IN PYTHON, NOT BY THE LLM
----------------------------------------------------------
Per the task spec ("Keep the LLM OUT of arithmetic; it interprets tool results, it doesn't
compute densities"), every tool below returns numbers/facts already computed deterministically
from the graph and CSVs -- rates, ratios, counts, spans. The agent's job in investigator.py
is entirely interpretive: given these facts, is this coordinated fraud or innocent
co-location? It never sees raw rows it would have to aggregate itself.

REUSE, DON'T RECOMPUTE
------------------------
Per PROGRESS.md's "things next session needs to know," this module is a thin, JSON-serializable
wrapping layer over what Stages 3-5 already built:
  - get_members / get_shared_entities  -> graph/build.py::internal_edges()
  - get_events                         -> data/events.csv + data/orders.csv, grouped by
                                           the customer id list the agent asks about
  - get_account_ages                   -> data/customers.csv `created_at`, relative to
                                           "now" = the dataset's own max created_at (so
                                           ages are stable/reproducible, not wall-clock)
  - compare_to_baseline                -> detect/features.py::compute_global_baseline() +
                                           compute_community_features() for the ONE
                                           community being investigated (never re-derives
                                           the six-feature score itself -- that's Stage 5's
                                           job; this just exposes the raw ingredients so the
                                           agent can read the same evidence Stage 5's score
                                           was built from, in human-readable form)

None of these tools ever return `ring_id` / `cluster_tag` (see graph/build.py's docstring --
those are ground-truth/debug labels, reading them here would be handing the agent the
answer key instead of evidence). The one exception is `demo/run_demo.py`-style tooling that
picks *which* community to feed the agent for the demo -- that happens outside these tools,
in investigator.py's `main()`, using ground truth only to choose an illustrative example to
show, exactly like scorer.py's diagnostic printout does.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import pandas as pd

from detect.features import GlobalBaseline, compute_community_features, compute_global_baseline
from graph.build import internal_edges

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"


@dataclass
class ToolContext:
    """Everything a tool call needs, built once per investigation run and threaded through
    every tool invocation -- avoids re-reading CSVs / rebuilding lookups on every call the
    agent makes within one community's investigation."""

    graph: nx.Graph
    customers: pd.DataFrame
    orders: pd.DataFrame
    events: pd.DataFrame
    baseline: GlobalBaseline

    @classmethod
    def build(cls, graph: nx.Graph, data_dir: Path = DATA_DIR) -> "ToolContext":
        customers = pd.read_csv(data_dir / "customers.csv", dtype=str)
        orders = pd.read_csv(data_dir / "orders.csv", dtype=str)
        events = pd.read_csv(data_dir / "events.csv", dtype=str)
        baseline = compute_global_baseline(customers, orders, events)
        return cls(graph=graph, customers=customers, orders=orders, events=events, baseline=baseline)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def get_members(ctx: ToolContext, community_id: int, communities: list[list[str]]) -> dict:
    """Who is in this community. `community_id` indexes into the `communities` list the
    investigator already has from detect.community -- this tool just resolves the id to
    member customer_ids plus each member's account-creation date, so the agent has names
    to refer to in every subsequent tool call."""
    members = communities[community_id]
    rows = ctx.customers[ctx.customers["customer_id"].isin(members)]
    member_info = [
        {"customer_id": r.customer_id, "created_at": r.created_at}
        for r in rows.itertuples(index=False)
    ]
    member_info.sort(key=lambda m: m["created_at"])
    return {"community_id": community_id, "size": len(members), "members": member_info}


def get_shared_entities(ctx: ToolContext, members: list[str]) -> dict:
    """Which devices/cards/phones/addresses/ip_subnets/pincodes are shared by >=2 of these
    members, with the edge weight (how discriminating that attribute type is) and exactly
    which members share it. This is the direct evidence for "coordinated" vs "coincidental" --
    a shared device/card is deliberate-reuse-strength evidence; a shared pincode/ip_subnet
    is weak, innocent-co-location-strength evidence. Wraps graph/build.py::internal_edges()."""
    edges = internal_edges(ctx.graph, members)
    shared = [
        {
            "entity": entity_node,
            "entity_type": edge_type,
            "weight": weight,
            "n_members_sharing": len(connected),
            "members_sharing": connected,
        }
        for entity_node, edge_type, weight, connected in edges
    ]
    total_weight = sum(s["weight"] for s in shared)
    high_weight_types = {"device", "card"}
    high_weight = sum(s["weight"] for s in shared if s["entity_type"] in high_weight_types)
    return {
        "shared_entities": shared,
        "n_shared_entities": len(shared),
        "total_internal_weight": round(total_weight, 3),
        "high_weight_fraction": round(high_weight / total_weight, 3) if total_weight > 0 else 0.0,
        "note": (
            "device/card sharing requires deliberate reuse (rare by accident); "
            "phone/address sharing is moderate; ip_subnet/pincode sharing is weak and "
            "common among unrelated people (whole neighborhoods/ISP blocks share these)."
        ),
    }


def get_events(ctx: ToolContext, members: list[str]) -> dict:
    """Chargeback/return/COD-refusal outcomes for these members, plus order volume and
    timing, so the agent can see not just "there were chargebacks" but "how many orders,
    over what period, and how concentrated in time." Never returns the community's
    ring-likelihood ratio -- that's compare_to_baseline's job; this is the raw behavioral
    ledger."""
    member_set = set(members)
    member_orders = ctx.orders[ctx.orders["customer_id"].isin(member_set)]
    member_events = ctx.events[ctx.events["customer_id"].isin(member_set)]

    event_counts = member_events["event_type"].value_counts().to_dict()
    n_orders = len(member_orders)
    n_bad_events = len(member_events)

    event_dates = pd.to_datetime(member_events["event_date"]) if n_bad_events else pd.Series([], dtype="datetime64[ns]")
    event_span_days = (
        round((event_dates.max() - event_dates.min()).total_seconds() / 86400.0, 1)
        if n_bad_events > 1
        else 0.0
    )

    promo_used = member_orders["promo_code"].fillna("").astype(str)
    promo_used = promo_used[promo_used != ""]
    promo_code_counts = promo_used.value_counts().to_dict()

    return {
        "n_orders": n_orders,
        "n_bad_events": n_bad_events,
        "event_counts_by_type": {
            "chargeback": int(event_counts.get("chargeback", 0)),
            "return": int(event_counts.get("return", 0)),
            "cod_refusal": int(event_counts.get("cod_refusal", 0)),
        },
        "community_event_rate": round(n_bad_events / n_orders, 4) if n_orders else 0.0,
        "bad_events_span_days": event_span_days,
        "promo_code_usage": promo_code_counts,
        "n_orders_with_promo": int(len(promo_used)),
    }


def get_account_ages(ctx: ToolContext, members: list[str]) -> dict:
    """Account-creation timing for these members: each member's age (days since account
    creation, relative to the dataset's own most-recent account -- not wall-clock "today",
    so this is reproducible across runs), and how tightly bunched the creation dates are.
    A ring created in a 1-10 day burst looks very different here from a family whose
    accounts were opened years apart."""
    member_rows = ctx.customers[ctx.customers["customer_id"].isin(members)]
    created = pd.to_datetime(member_rows["created_at"])
    reference_date = pd.to_datetime(ctx.customers["created_at"]).max()

    ages_days = ((reference_date - created) / pd.Timedelta(days=1)).round(1)
    span_days = round((created.max() - created.min()).total_seconds() / 86400.0, 1) if len(created) > 1 else 0.0

    per_member = [
        {"customer_id": cid, "created_at": str(dt.date()), "account_age_days": float(age)}
        for cid, dt, age in zip(member_rows["customer_id"], created, ages_days)
    ]
    per_member.sort(key=lambda m: m["created_at"])

    return {
        "members": per_member,
        "creation_span_days": span_days,
        "dataset_reference_date": str(reference_date.date()),
        "dataset_full_span_days": round(ctx.baseline.created_at_span_days, 1),
        "note": (
            "creation_span_days is how many days apart this group's accounts were "
            "created; compare it against dataset_full_span_days (the whole dataset's "
            "creation window) to judge whether this group arrived unusually close "
            "together or is spread out like an ordinary population sample."
        ),
    }


def compare_to_baseline(ctx: ToolContext, community_id: int, communities: list[list[str]]) -> dict:
    """How far above the dataset-wide normal rate this community's fraud signals are.
    Runs detect/features.py's real feature computation for this ONE community (the same
    code Stage 5's scorer uses) and surfaces the raw, human-readable numbers -- not the
    squashed [0,1] sub-scores scorer.py computes for ranking, since the agent should reason
    from the actual ratios/rates, not a pre-squashed score that already encodes a judgment
    call about "how much is a lot"."""
    features_list = compute_community_features(ctx.graph, communities, data_dir=DATA_DIR)
    features = next(f for f in features_list if f.community_index == community_id)

    return {
        "community_event_rate": round(features.community_event_rate, 4),
        "global_baseline_event_rate": round(ctx.baseline.event_rate, 4),
        "event_rate_ratio": round(features.event_rate_ratio, 2),
        "high_weight_edge_share": round(features.high_weight_edge_share, 3),
        "timing_burst_score": round(features.timing_burst_score, 3),
        "fresh_account_ratio": round(features.fresh_account_ratio, 3),
        "promo_concentration": round(features.promo_concentration, 3),
        "n_orders": features.n_orders,
        "n_bad_events": features.n_bad_events,
        "interpretation_guide": {
            "event_rate_ratio": "1.0 = exactly baseline; this dataset's injected rings run 9-16x baseline; ~1x is normal.",
            "high_weight_edge_share": "0-1 share of internal shared-entity evidence weight coming from device/card (deliberate) vs phone/address/ip_subnet/pincode (innocent-compatible). Near 0 means the sharing here is the weak/common kind.",
            "timing_burst_score": "0-1, how much tighter this group's account-creation spread is than a same-size random sample would typically be. Near 0 = spread out like ordinary independent signups.",
            "fresh_account_ratio": "0-1 fraction of members created within the same 14-day window. Near 1 with an old, unrelated population would be surprising; near 1 for a just-formed group is expected for a ring.",
            "promo_concentration": "0-1; near 1 means the group is farming one specific promo code; 0 means no notable promo concentration.",
        },
    }


# ---------------------------------------------------------------------------
# OpenAI function-calling tool definitions (Chat Completions `tools` shape: each entry is
# {"type": "function", "function": {name, description, parameters}} -- see
# agent/investigator.py's module docstring for why this is a manual loop, not the
# Assistants/Responses API)
# ---------------------------------------------------------------------------

_NO_ARG_PARAMETERS = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _function_tool(name: str, description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _NO_ARG_PARAMETERS,
        },
    }


TOOL_DEFINITIONS = [
    _function_tool(
        "get_members",
        "Get the list of customer_ids in this suspicious community, with their "
        "account-creation dates. Call this first to see who you're investigating.",
    ),
    _function_tool(
        "get_shared_entities",
        "Get which devices, cards, phones, addresses, ip_subnets, and pincodes are "
        "shared by 2+ members of this community, with edge weight (how discriminating/"
        "rare each attribute type is) and which members share each one. Device/card "
        "sharing is strong deliberate-coordination evidence; ip_subnet/pincode sharing "
        "is weak and common among totally unrelated people.",
    ),
    _function_tool(
        "get_events",
        "Get chargeback/return/COD-refusal counts, order volume, and promo-code usage "
        "for this community's members. Use this to check for elevated loss-causing "
        "behavior and promo farming.",
    ),
    _function_tool(
        "get_account_ages",
        "Get each member's account-creation date and age, and how tightly bunched "
        "(burst-created) this group's signups are compared to the dataset's full time "
        "window. Use this to check for a fresh-account burst (ring signature) vs. "
        "accounts opened independently over time (benign).",
    ),
    _function_tool(
        "compare_to_baseline",
        "Get this community's behavioral signals (event rate, high-weight-edge share, "
        "timing burst, fresh-account ratio, promo concentration) compared against the "
        "dataset-wide baseline, with an interpretation guide for what each number means. "
        "Use this to quantify how far above/below normal this community is on each axis.",
    ),
]


def execute_tool(ctx: ToolContext, community_id: int, communities: list[list[str]], name: str, tool_input: dict) -> dict:
    """Dispatch a tool call by name. `members` is always re-derived from `community_id`
    rather than trusted from the LLM's input, so the agent cannot (accidentally or
    otherwise) ask about a different set of customers than the one it was assigned to
    investigate -- every tool call is scoped to exactly one community."""
    members = communities[community_id]
    if name == "get_members":
        return get_members(ctx, community_id, communities)
    if name == "get_shared_entities":
        return get_shared_entities(ctx, members)
    if name == "get_events":
        return get_events(ctx, members)
    if name == "get_account_ages":
        return get_account_ages(ctx, members)
    if name == "compare_to_baseline":
        return compare_to_baseline(ctx, community_id, communities)
    raise ValueError(f"unknown tool: {name!r}")
