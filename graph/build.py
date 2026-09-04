"""Stage 3: build the heterogeneous weighted graph of customers, devices, addresses, cards,
phones, and IPs.

GRAPH SHAPE: BIPARTITE, NOT PROJECTED
--------------------------------------
We keep the graph **bipartite** (customer nodes <-> shared-attribute-entity nodes), rather
than eagerly projecting it down to a customer-only graph with one weighted edge per pair
that shares something. Two reasons:

1. **Community detection needs the entity nodes.** Louvain/Leiden (Stage 4) works fine on
   a bipartite graph -- entity nodes with only a handful of customer-neighbors just become
   part of whichever customer community dominates them. But going the other direction is
   lossy: once you collapse "customer A and B share DEVICE_ENT001" into a single weighted
   A-B edge, you've thrown away *which* device, and thus the agent investigator (Stage 7)
   loses its ability to say "these 7 accounts all used device DEVICE_ENT001279" -- which is
   exactly the evidence subgraph the demo needs to render.
2. **Projection is a many-to-many blowup.** An entity shared by k customers becomes
   C(k, 2) projected edges. For a low-weight, high-degree type like ip_subnet or pincode
   (shared by hundreds of unrelated customers), that's a combinatorial explosion of noisy
   edges. Left bipartite, it's one entity node with k edges -- O(k), not O(k^2) -- and
   Stage 4/5 can decide how much weight that hub contributes without ever materializing
   the pairwise blowup.

`customer_projection()` below is provided as a helper for anything downstream that *does*
want the pairwise view (e.g. a quick visualization or a sanity check), but the graph this
module builds and that `make graph` operates on is the bipartite one.

NODE TYPES
----------
  customer    one per row in data/customers.csv
  device      one per canonical device_entity_id (from Stage 2's resolved_devices.csv)
  card        one per canonical card_entity_id
  address     one per canonical address_entity_id (fuzzy-resolved near-duplicates merged)
  phone       one per canonical phone_entity_id (E.164-normalized)
  ip_subnet   one per canonical ip_entity_id (a resolved /24 subnet)
  pincode     one per raw postal pincode (data/addresses.csv `pincode` column -- Stage 2
              does not resolve pincode as its own entity, it's a raw grouping key used only
              as the weakest background-noise edge type; see graph/weights.py)

EDGE TYPES
----------
customer --(device|card|phone|address|ip_subnet|pincode)--> entity, weighted per
graph/weights.py. Edges carry `edge_type` and `weight` attributes.

NODE/EDGE ATTRIBUTES KEPT FOR LATER STAGES
-------------------------------------------
  customer nodes: created_at, ring_id, cluster_tag (ground-truth/debug labels -- NOT used
                  by community detection or scoring, only for eval/demo), event counts
                  (n_chargebacks, n_returns, n_cod_refusals, n_events) -- Stage 4's
                  features.py needs chargeback/return/COD-refusal density per community.
  entity nodes:   entity_type, degree is implicit via networkx but we also store it
                  explicitly for quick inspection without recomputing.
  edges:          edge_type, weight, created_at (the customer-side event's created_at,
                  i.e. when this particular attribute usage was recorded) -- lets later
                  stages reason about *when* a shared link was formed (burst detection).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import pandas as pd

from graph.weights import load_edge_weights

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
RESOLVED_DIR = DATA_DIR / "resolved"

# (edge_type, resolved csv filename, id column, entity-id column)
ENTITY_SOURCES = [
    ("device", "resolved_devices.csv", "device_row_id", "device_entity_id"),
    ("card", "resolved_cards.csv", "card_row_id", "card_entity_id"),
    ("phone", "resolved_phones.csv", "phone_id", "phone_entity_id"),
    ("address", "resolved_addresses.csv", "address_id", "address_entity_id"),
    ("ip_subnet", "resolved_ips.csv", "ip_id", "ip_entity_id"),
]


@dataclass
class GraphBuildResult:
    graph: nx.Graph
    weights: dict[str, float]


def _load_customers(data_dir: Path) -> pd.DataFrame:
    customers = pd.read_csv(data_dir / "customers.csv", dtype=str)
    customers["ring_id"] = customers["ring_id"].fillna("")
    customers["cluster_tag"] = customers["cluster_tag"].fillna("")
    return customers


def _load_event_counts(data_dir: Path) -> pd.DataFrame:
    """Per-customer counts of chargeback/return/cod_refusal events, needed by Stage 4."""
    events = pd.read_csv(data_dir / "events.csv", dtype=str)
    counts = (
        events.groupby(["customer_id", "event_type"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["chargeback", "return", "cod_refusal"], fill_value=0)
    )
    counts.columns = [f"n_{c}" for c in counts.columns]
    counts["n_events"] = counts.sum(axis=1)
    return counts


def _load_pincodes(data_dir: Path) -> pd.DataFrame:
    """Pincode isn't resolved into its own entity by Stage 2 -- it's a raw grouping key
    pulled straight from data/addresses.csv, used only as the lowest-weight edge type."""
    addresses = pd.read_csv(data_dir / "addresses.csv", dtype=str)
    return addresses[["customer_id", "pincode"]].rename(
        columns={"pincode": "pincode_entity_id"}
    )


def build_graph(
    data_dir: Path = DATA_DIR,
    resolved_dir: Path = RESOLVED_DIR,
    weights: dict[str, float] | None = None,
) -> GraphBuildResult:
    """Build the bipartite heterogeneous graph. Deterministic: node/edge order follows
    input CSV row order, and no randomness is involved."""
    weights = weights or load_edge_weights()

    customers = _load_customers(data_dir)
    event_counts = _load_event_counts(data_dir)

    graph = nx.Graph()

    for row in customers.itertuples(index=False):
        counts = (
            event_counts.loc[row.customer_id]
            if row.customer_id in event_counts.index
            else pd.Series({"n_chargeback": 0, "n_return": 0, "n_cod_refusal": 0, "n_events": 0})
        )
        graph.add_node(
            row.customer_id,
            node_type="customer",
            created_at=row.created_at,
            ring_id=row.ring_id,
            cluster_tag=row.cluster_tag,
            n_chargebacks=int(counts["n_chargeback"]),
            n_returns=int(counts["n_return"]),
            n_cod_refusals=int(counts["n_cod_refusal"]),
            n_events=int(counts["n_events"]),
        )

    created_at_by_customer = customers.set_index("customer_id")["created_at"]

    for edge_type, filename, _id_col, entity_col in ENTITY_SOURCES:
        df = pd.read_csv(resolved_dir / filename, dtype=str)
        weight = weights[edge_type]
        for row in df.itertuples(index=False):
            entity_id = getattr(row, entity_col)
            customer_id = row.customer_id
            if pd.isna(entity_id) or pd.isna(customer_id):
                continue
            node_id = f"{edge_type}:{entity_id}"
            if node_id not in graph:
                graph.add_node(node_id, node_type=edge_type, entity_id=entity_id)
            graph.add_edge(
                customer_id,
                node_id,
                edge_type=edge_type,
                weight=weight,
                created_at=created_at_by_customer.get(customer_id),
            )

    pincodes = _load_pincodes(data_dir)
    pincode_weight = weights["pincode"]
    for row in pincodes.itertuples(index=False):
        if pd.isna(row.pincode_entity_id) or pd.isna(row.customer_id):
            continue
        node_id = f"pincode:{row.pincode_entity_id}"
        if node_id not in graph:
            graph.add_node(node_id, node_type="pincode", entity_id=row.pincode_entity_id)
        graph.add_edge(
            row.customer_id,
            node_id,
            edge_type="pincode",
            weight=pincode_weight,
            created_at=created_at_by_customer.get(row.customer_id),
        )

    return GraphBuildResult(graph=graph, weights=weights)


def customer_projection(graph: nx.Graph) -> nx.Graph:
    """Collapse the bipartite graph down to a customer-only weighted graph, for callers
    that want the pairwise view (e.g. quick visualization). Not used by community
    detection -- see the module docstring for why the bipartite graph is kept as the
    canonical structure.

    Two customers sharing multiple entities get a single projected edge whose weight is
    the SUM of the weights of every entity they share (so 7 accounts sharing both a
    device AND a card end up with a stronger projected edge than two that only share a
    pincode).
    """
    customers = [n for n, d in graph.nodes(data=True) if d["node_type"] == "customer"]
    projection = nx.Graph()
    for c in customers:
        projection.add_node(c, **graph.nodes[c])

    for entity_node, entity_data in graph.nodes(data=True):
        if entity_data["node_type"] == "customer":
            continue
        neighbors = list(graph.neighbors(entity_node))
        for i in range(len(neighbors)):
            for j in range(i + 1, len(neighbors)):
                a, b = neighbors[i], neighbors[j]
                edge_weight = graph.edges[entity_node, a]["weight"]
                if projection.has_edge(a, b):
                    projection.edges[a, b]["weight"] += edge_weight
                    projection.edges[a, b]["shared_entities"].append(entity_node)
                else:
                    projection.add_edge(a, b, weight=edge_weight, shared_entities=[entity_node])

    return projection


def graph_stats(graph: nx.Graph) -> dict:
    node_counts: dict[str, int] = {}
    for _, data in graph.nodes(data=True):
        node_counts[data["node_type"]] = node_counts.get(data["node_type"], 0) + 1

    edge_counts: dict[str, int] = {}
    for _, _, data in graph.edges(data=True):
        edge_counts[data["edge_type"]] = edge_counts.get(data["edge_type"], 0) + 1

    return {
        "total_nodes": graph.number_of_nodes(),
        "total_edges": graph.number_of_edges(),
        "nodes_by_type": node_counts,
        "edges_by_type": edge_counts,
    }


def internal_edges(graph: nx.Graph, customer_ids: list[str]) -> list[tuple]:
    """Edges among a given set of customers' shared-entity neighborhoods, restricted to
    entities that connect at least two of the given customers -- i.e. the "internal"
    evidence links for a ring or family, ignoring entities only one member touches."""
    customer_set = set(customer_ids)
    results = []
    seen_entities = set()
    for c in customer_ids:
        for entity_node in graph.neighbors(c):
            if entity_node in seen_entities:
                continue
            seen_entities.add(entity_node)
            members = [n for n in graph.neighbors(entity_node) if n in customer_set]
            if len(members) < 2:
                continue
            edge_type = graph.nodes[entity_node]["node_type"]
            weight = graph.edges[members[0], entity_node]["weight"]
            results.append((entity_node, edge_type, weight, members))
    results.sort(key=lambda r: (-r[2], r[0]))
    return results


def main() -> None:
    result = build_graph()
    stats = graph_stats(result.graph)

    print("=== Stage 3: graph build stats ===")
    print(f"Total nodes: {stats['total_nodes']}")
    for node_type, count in sorted(stats["nodes_by_type"].items()):
        print(f"  {node_type:12s} {count}")
    print(f"Total edges: {stats['total_edges']}")
    for edge_type, count in sorted(stats["edges_by_type"].items()):
        weight = result.weights[edge_type]
        print(f"  {edge_type:12s} {count:6d}  (weight={weight})")

    customers = _load_customers(DATA_DIR)
    ring_col = customers[customers["ring_id"] != ""]
    if not ring_col.empty:
        sample_ring_id = sorted(ring_col["ring_id"].unique())[0]
        ring_members = ring_col[ring_col["ring_id"] == sample_ring_id]["customer_id"].tolist()
        _print_internal_edges(
            result.graph, f"Ring {sample_ring_id}", ring_members
        )

    family_tags = sorted(
        t for t in customers["cluster_tag"].unique() if t.startswith("family_")
    )
    if family_tags:
        sample_family = family_tags[0]
        family_members = customers[customers["cluster_tag"] == sample_family][
            "customer_id"
        ].tolist()
        _print_internal_edges(result.graph, f"Family {sample_family}", family_members)


def _print_internal_edges(graph: nx.Graph, label: str, members: list[str]) -> None:
    print(f"\n=== {label} ({len(members)} members): internal shared-entity edges ===")
    edges = internal_edges(graph, members)
    if not edges:
        print("  (no shared entities among members)")
        return
    for entity_node, edge_type, weight, connected in edges:
        print(
            f"  [{edge_type:9s} w={weight:.1f}] {entity_node} "
            f"<- shared by {len(connected)}/{len(members)} members: {connected}"
        )
    total_weight = sum(w for _, _, w, _ in edges)
    high_weight = sum(w for _, t, w, _ in edges if t in ("device", "card"))
    print(
        f"  -> {len(edges)} internal shared entities, total weight {total_weight:.1f}, "
        f"of which {high_weight:.1f} from HIGH-weight (device/card) edges"
    )


if __name__ == "__main__":
    main()
