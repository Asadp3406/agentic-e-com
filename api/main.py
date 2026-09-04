"""Stage 10: FastAPI wrapper exposing the Part-A engine as the B3 endpoints.

NO DETECTION LOGIC LIVES HERE
--------------------------------
Every endpoint below calls existing Stage 3-9 code directly (`graph.build`,
`detect.community`, `detect.scorer`, `agent.investigator`, `agent.policy`,
`eval.run_eval`) and reshapes the result into render-ready JSON -- this module is pure
orchestration + JSON shaping, the same "reuse, don't recompute" convention every earlier
stage followed (see `demo/run_demo.py`'s docstring, whose call sequence this mirrors for
`POST /api/run`).

IN-MEMORY RUN CACHE
----------------------
`POST /api/run` builds the graph, detects communities, and scores them once, and stashes
the result in a single module-level `_RUN` object. Every GET endpoint reads from that
cache rather than recomputing anything, so `/api/graph`, `/api/rings`, `/api/rings/{id}`,
and `/api/metrics` all describe the SAME run. This is intentionally a single global (not
a dict of run_id -> state) -- the task spec asks for "cache the result in memory," and a
one-person local demo tool has no need for concurrent multi-run storage. `run_id` is
still returned and threaded through so the frontend has something stable to display /
compare against, and a stale-cache GET (before any POST /api/run) returns 409 rather than
silently 500ing.

CASE FILES VS. LIVE INVESTIGATION FOR /api/rings/{id}
----------------------------------------------------------
`agent/case_files/*.json` (per Stage 7/9) already has everything this endpoint's case-file
payload needs. Rather than re-running a live LLM investigation on every GET (slow, costs
money, and non-deterministic per Stage 9's finding that two real runs of the same
community can disagree), `/api/rings/{id}` serves the case file already on disk if one
exists for that community, and otherwise runs a fresh investigation on demand and writes
one -- so the first click on a never-investigated ring is slower but every subsequent
click (including a page refresh) is instant and stable.

EVIDENCE SUBGRAPH AS JSON, NOT A PNG
----------------------------------------
`demo/run_demo.py::render_evidence_subgraph` and `graph/visualize.py`'s `_draw` are
matplotlib PNG renderers -- fine for the engine-level demo, wrong shape for
`react-force-graph`. `_subgraph_for` (the node-selection logic, imported from
`graph/visualize.py`) is reused as-is; only the output shaping is new here
(`_graph_payload`, shared between `/api/graph` and the evidence-subgraph field of
`/api/rings/{id}`).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from agent.investigator import investigate_community
from agent.policy import CASE_FILE_DIR, decide_action, load_cost_model, write_case_file
from detect.community import CommunityResult, detect_communities
from detect.scorer import CommunityScore, dominant_feature, score_communities
from graph.build import build_graph, internal_edges
from graph.visualize import _subgraph_for

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

MIN_REPORTED_SIZE = 3

app = FastAPI(title="Abuse Ring Sentinel API")

# CORS for the Vite dev server (default port 5173; 4173 for `vite preview`).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# In-memory run cache
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    run_id: str
    graph: nx.Graph
    community_result: CommunityResult
    scores: list[CommunityScore]
    ring_by_customer: dict[str, str]
    tag_by_customer: dict[str, str]


_RUN: RunState | None = None


def _require_run() -> RunState:
    if _RUN is None:
        raise HTTPException(status_code=409, detail="No run yet -- call POST /api/run first.")
    return _RUN


def _ground_truth_label(members: list[str], run: RunState) -> str:
    """Debug/report-only label for display -- never fed back into detection. Mirrors
    demo/run_demo.py::_ground_truth_label."""
    from collections import Counter

    ring_ids = [run.ring_by_customer.get(m, "") for m in members if run.ring_by_customer.get(m, "")]
    if ring_ids:
        top_ring, count = Counter(ring_ids).most_common(1)[0]
        return f"{top_ring} ({count}/{len(members)})"
    tags = [run.tag_by_customer.get(m, "") for m in members if run.tag_by_customer.get(m, "")]
    if tags:
        base_tags = [t.rsplit("_", 1)[0] if t[-1].isdigit() else t for t in tags]
        base, count = Counter(base_tags).most_common(1)[0]
        return f"benign:{base} ({count}/{len(members)})"
    return "unlabeled"


def _status_for(cs: CommunityScore, run: RunState) -> str:
    """suspicious / cleared / unlabeled, purely for display -- ground truth is used ONLY
    to annotate the response for the UI, exactly as every eval/demo script already does;
    the score/rank itself is unaffected."""
    label = _ground_truth_label(cs.members, run)
    if label.startswith("benign"):
        return "cleared"
    if label == "unlabeled":
        return "unlabeled"
    return "suspicious"


# ---------------------------------------------------------------------------
# Graph JSON shaping (shared by /api/graph and /api/rings/{id}'s evidence subgraph)
# ---------------------------------------------------------------------------


def _graph_payload(sub: nx.Graph, run: RunState) -> dict[str, Any]:
    """Nodes + edges shaped for react-force-graph. Every customer node's `cluster_id` is
    the index of the community it belongs to in this run (or None if it's not in any
    size>=3 community); `suspicious` / `risk` come from that community's score, so a
    ring's members render red/large and everyone else renders neutral, driven entirely by
    already-computed Stage 4/5 output -- no new detection logic."""
    community_of: dict[str, int] = {}
    for idx, members in enumerate(run.community_result.communities):
        for m in members:
            community_of[m] = idx
    score_by_community = {cs.community_index: cs for cs in run.scores}

    nodes = []
    for node_id, data in sub.nodes(data=True):
        node_type = data["node_type"]
        if node_type == "customer":
            cluster_id = community_of.get(node_id)
            cs = score_by_community.get(cluster_id) if cluster_id is not None else None
            risk = cs.score if cs is not None else 0.0
            suspicious = bool(
                cs is not None and cs.size >= MIN_REPORTED_SIZE and _status_for(cs, run) == "suspicious"
            )
            nodes.append(
                {
                    "id": node_id,
                    "type": "customer",
                    "cluster_id": cluster_id,
                    "suspicious": suspicious,
                    "risk": round(risk, 4),
                    "ring_id": run.ring_by_customer.get(node_id) or None,
                    "cluster_tag": run.tag_by_customer.get(node_id) or None,
                }
            )
        else:
            nodes.append(
                {
                    "id": node_id,
                    "type": node_type,
                    "cluster_id": None,
                    "suspicious": False,
                    "risk": 0.0,
                    "entity_id": data.get("entity_id"),
                }
            )

    edges = [
        {"source": u, "target": v, "type": data["edge_type"], "weight": data["weight"]}
        for u, v, data in sub.edges(data=True)
    ]

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# POST /api/run
# ---------------------------------------------------------------------------


@app.post("/api/run")
def run_pipeline() -> dict[str, Any]:
    """Run the full engine (graph -> communities -> scores), cache in memory, return a
    run_id. Mirrors demo/run_demo.py's steps 1-2 -- investigation (step 3+) happens lazily
    per-ring in GET /api/rings/{id}, not eagerly for every community here, since running a
    real LLM call for every community on every POST /api/run would be slow and expensive
    for no benefit until a human actually opens that ring's case file."""
    global _RUN

    build_result = build_graph()
    graph = build_result.graph
    community_result = detect_communities(graph)
    scores = score_communities(graph, community_result.communities)

    customers = pd.read_csv(DATA_DIR / "customers.csv", dtype=str)
    ring_by_customer = dict(zip(customers["customer_id"], customers["ring_id"].fillna("")))
    tag_by_customer = dict(zip(customers["customer_id"], customers["cluster_tag"].fillna("")))

    run_id = uuid.uuid4().hex[:12]
    _RUN = RunState(
        run_id=run_id,
        graph=graph,
        community_result=community_result,
        scores=scores,
        ring_by_customer=ring_by_customer,
        tag_by_customer=tag_by_customer,
    )

    reportable = [s for s in scores if s.size >= MIN_REPORTED_SIZE]
    return {
        "run_id": run_id,
        "graph_nodes": graph.number_of_nodes(),
        "graph_edges": graph.number_of_edges(),
        "n_communities": community_result.n_communities,
        "n_reportable_communities": len(reportable),
        "method": community_result.method,
        "giant_flags": community_result.giant_flags,
    }


# ---------------------------------------------------------------------------
# GET /api/graph
# ---------------------------------------------------------------------------


@app.get("/api/graph")
def get_graph() -> dict[str, Any]:
    """The full network, shaped for react-force-graph. Restricted to customers in a
    size>=3 community plus their directly-shared entities (the full bipartite graph
    including every singleton pincode/ip_subnet node would be too dense to render
    usefully) -- entities touched only by community-less customers are dropped."""
    run = _require_run()

    interesting_members: set[str] = set()
    for members in run.community_result.communities:
        if len(members) >= MIN_REPORTED_SIZE:
            interesting_members.update(members)

    sub = _subgraph_for(run.graph, list(interesting_members))
    return {"run_id": run.run_id, **_graph_payload(sub, run)}


# ---------------------------------------------------------------------------
# GET /api/rings
# ---------------------------------------------------------------------------


@app.get("/api/rings")
def list_rings() -> dict[str, Any]:
    """Ranked communities: id, size, score, rupee_risk, status. `rupee_risk` reuses
    agent/policy.py's own per-member convention (size * chargeback_cost) so this number
    matches what a case file would report for the same community, without requiring a
    live investigation to compute it."""
    run = _require_run()
    cost_model = load_cost_model()

    reportable = [s for s in run.scores if s.size >= MIN_REPORTED_SIZE]
    rings = [
        {
            "id": cs.community_index,
            "size": cs.size,
            "score": round(cs.score, 4),
            "rupee_risk": round(cs.size * cost_model.chargeback_cost, 2),
            "status": _status_for(cs, run),
            "top_feature": dominant_feature(cs),
            "ground_truth": _ground_truth_label(cs.members, run),
        }
        for cs in reportable
    ]
    return {"run_id": run.run_id, "rings": rings}


# ---------------------------------------------------------------------------
# GET /api/rings/{id}
# ---------------------------------------------------------------------------


@app.get("/api/rings/{community_id}")
def get_ring(community_id: int) -> dict[str, Any]:
    """Case file for one community: members, shared entities, evidence, agent reasoning,
    recommended action, confidence, and an evidence subgraph shaped for the graph lib.
    Serves an existing case file from disk if one was already written (Stage 7/9's
    `agent/case_files/community_<id>.json`); otherwise runs a fresh investigation via the
    real agent + policy and writes one, so subsequent requests for the same ring are
    served from disk rather than re-invoking the LLM."""
    run = _require_run()
    communities = run.community_result.communities

    if community_id < 0 or community_id >= len(communities):
        raise HTTPException(status_code=404, detail=f"No community #{community_id} in this run.")

    members = communities[community_id]
    cs = next((s for s in run.scores if s.community_index == community_id), None)

    case_file_path = CASE_FILE_DIR / f"community_{community_id}.json"
    if not case_file_path.exists():
        result = investigate_community(run.graph, communities, community_id)
        cost_model = load_cost_model()
        decision = decide_action(result, cost_model)
        write_case_file(result, decision)
    case_file = json.loads(case_file_path.read_text())

    sub = _subgraph_for(run.graph, members)
    evidence_subgraph = _graph_payload(sub, run)

    return {
        "run_id": run.run_id,
        "community_id": community_id,
        "size": len(members),
        "score": round(cs.score, 4) if cs else None,
        "status": _status_for(cs, run) if cs else "unlabeled",
        "ground_truth": _ground_truth_label(members, run),
        "members": case_file["members"],
        "shared_entities": [
            {"entity": node, "type": etype, "weight": weight, "member_count": len(connected)}
            for node, etype, weight, connected in internal_edges(run.graph, members)
        ],
        "verdict": case_file["verdict"],
        "degraded": case_file["degraded"],
        "degraded_reason": case_file["degraded_reason"],
        "policy_decision": case_file["policy_decision"],
        "tool_call_trail": case_file["tool_call_trail"],
        "evidence_subgraph": evidence_subgraph,
    }


# ---------------------------------------------------------------------------
# GET /api/metrics
# ---------------------------------------------------------------------------


@app.get("/api/metrics")
def get_metrics() -> dict[str, Any]:
    """P/R, rupee FP-cost curve points, baseline-vs-graph delta. Reuses eval/run_eval.py's
    functions directly rather than recomputing the eval -- ground truth is read only
    inside that module, per its own docstring's fail-loudly convention."""
    run = _require_run()

    from baseline.txn_classifier import run_baseline
    from eval.run_eval import (
        HEAD_TO_HEAD_BUDGETS,
        _account_scores,
        _label_communities,
        _load_ground_truth,
        account_level_metrics,
        cost_sweep,
        graph_recall_at_budget,
        money_optimal_point,
        ring_level_metrics,
    )
    from eval.run_eval import load_cost_model as eval_load_cost_model

    cost_model = eval_load_cost_model()
    gt, ring_by_customer, tag_by_customer = _load_ground_truth()
    all_customer_ids = gt["account_id"].tolist()
    account_score = _account_scores(run.scores, all_customer_ids)

    labeled_communities = _label_communities(run.scores, ring_by_customer, tag_by_customer)

    sweep_points = cost_sweep(account_score, gt, cost_model)
    optimal = money_optimal_point(sweep_points)
    threshold = optimal.threshold

    ring_metrics = ring_level_metrics(labeled_communities, threshold)
    account_metrics = account_level_metrics(account_score, gt, threshold)

    baseline_result = run_baseline()
    head_to_head = []
    for budget in HEAD_TO_HEAD_BUDGETS:
        g_recall, g_precision, n_flagged = graph_recall_at_budget(account_score, gt, budget)
        b_metrics = baseline_result.budget_results[budget]
        head_to_head.append(
            {
                "budget": budget,
                "n_flagged": n_flagged,
                "graph_recall": round(g_recall, 4),
                "graph_precision": round(g_precision, 4),
                "baseline_recall": round(b_metrics["ring_recall"], 4),
                "baseline_precision": round(b_metrics["ring_precision"], 4),
            }
        )

    return {
        "run_id": run.run_id,
        "money_optimal_threshold": round(threshold, 4),
        "cost_sweep": [
            {
                "threshold": round(p.threshold, 4),
                "n_flagged": p.n_flagged,
                "ring_accounts_caught": p.ring_accounts_caught,
                "legit_accounts_blocked": p.legit_accounts_blocked,
                "rupees_saved": p.rupees_saved,
                "rupees_lost": p.rupees_lost,
                "net_rupees": p.net_rupees,
            }
            for p in sweep_points
        ],
        "money_optimal_point": {
            "threshold": round(optimal.threshold, 4),
            "net_rupees": optimal.net_rupees,
            "rupees_saved": optimal.rupees_saved,
            "rupees_lost": optimal.rupees_lost,
            "ring_accounts_caught": optimal.ring_accounts_caught,
            "legit_accounts_blocked": optimal.legit_accounts_blocked,
        },
        "ring_level_metrics": ring_metrics,
        "account_level_metrics": account_metrics,
        "head_to_head": head_to_head,
    }


# ---------------------------------------------------------------------------
# GET /api/benign
# ---------------------------------------------------------------------------


@app.get("/api/benign")
def get_benign() -> dict[str, Any]:
    """Benign clusters + how they were cleared: at the money-optimal threshold (same one
    /api/metrics reports), plus each cluster's dominant feature so the UI can show *why*
    it was cleared (e.g. "only pincode/ip_subnet sharing, baseline event rate")."""
    run = _require_run()

    from eval.run_eval import _account_scores, _label_communities, _load_ground_truth, cost_sweep, money_optimal_point
    from eval.run_eval import load_cost_model as eval_load_cost_model

    cost_model = eval_load_cost_model()
    gt, ring_by_customer, tag_by_customer = _load_ground_truth()
    all_customer_ids = gt["account_id"].tolist()
    account_score = _account_scores(run.scores, all_customer_ids)
    sweep_points = cost_sweep(account_score, gt, cost_model)
    threshold = money_optimal_point(sweep_points).threshold

    labeled_communities = _label_communities(run.scores, ring_by_customer, tag_by_customer)

    benign = []
    for lc in labeled_communities:
        if lc.label != "benign":
            continue
        benign.append(
            {
                "id": lc.cs.community_index,
                "cluster_tag": lc.detail,
                "size": lc.cs.size,
                "score": round(lc.cs.score, 4),
                "flagged": lc.cs.score >= threshold,
                "top_feature": dominant_feature(lc.cs),
                "sub_scores": {k: round(v, 4) for k, v in lc.cs.sub_scores.items()},
            }
        )
    benign.sort(key=lambda r: (-r["flagged"], -r["score"]))

    n_flagged = sum(1 for r in benign if r["flagged"])
    return {
        "run_id": run.run_id,
        "threshold": round(threshold, 4),
        "n_benign_clusters": len(benign),
        "n_false_positives": n_flagged,
        "benign_clusters": benign,
    }
