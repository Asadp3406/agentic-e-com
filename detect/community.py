"""Stage 4: weighted community detection over the heterogeneous customer graph.

WHAT THIS DOES
---------------
Runs weighted community detection over Stage 3's bipartite graph (customer nodes +
device/card/phone/address/ip_subnet/pincode entity nodes, edges weighted by
discriminativeness -- see graph/weights.py) and returns communities as clusters of
*customer* ids. Community detection itself doesn't care that the graph is bipartite:
entity nodes with only a couple of low-weight neighbors just get pulled into whichever
customer community dominates them, and get dropped from the output since Stage 5+ only
cares about customer clusters.

ALGORITHM CHOICE: LEIDEN PREFERRED, LOUVAIN FALLBACK
------------------------------------------------------
Leiden (via leidenalg + python-igraph) is preferred over Louvain (python-louvain) because
Louvain has a known defect: it can produce internally-disconnected "communities" (a
consequence of its local-moving phase), while Leiden guarantees every community it
returns is internally connected. That matters here because a disconnected "community" of
customers who don't actually share any evidence with each other would be exactly the kind
of case Stage 7's agent needs to be able to see and reason about honestly. Louvain is kept
as a fallback (`method="louvain"` or auto-fallback if leidenalg/igraph aren't importable)
so this module still works in a slimmer environment.

Both run on the *weighted* graph using edge `weight` (rarity-weighted per graph/weights.py)
as the CPM/modularity edge weight, at `community_resolution` from config.yaml (see there
for what changing it does -- higher = more, smaller communities).

THE GIANT-BLOB RISK
--------------------
Louvain/Leiden modularity-style detection can produce one dominant "everything is
connected to everything" community if low-weight, high-degree entity nodes (pincode,
ip_subnet) chain together otherwise-unrelated customers through long paths (customer A
and customer Z never share anything directly, but A-pincode1-B-pincode2-...-Z). This
doesn't crash the algorithm, it just produces a useless mega-cluster that swallows most
of the customer base and makes ring detection impossible (a 3-ring hiding inside a
1000-customer blob is invisible to any per-community feature in Stage 5).

We don't try to silently "fix" this by re-parameterizing until it goes away -- that's
overfitting the algorithm to this specific synthetic dataset. Instead we detect it
(`flag_giant_communities`, any community above `giant_community_frac` of all clustered
customers) and surface it loudly in the returned `CommunityResult.giant_flags` /
`main()`'s printed output, so it becomes a concrete tuning note (raise
`community_resolution`, or drop pincode/ip_subnet edges before detection) rather than a
silent quality failure discovered three stages later.
"""

from __future__ import annotations

import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import yaml

from graph.build import build_graph

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config.yaml"

# A community above this fraction of all clustered customers is flagged as an
# implausible giant blob (see module docstring). 15 synthetic rings of size 4-9 sit in
# ~1600 customers total, so any single "community" claiming e.g. >20% of everyone is
# clearly not a coordinated-fraud cluster -- it's the algorithm failing to separate
# noise-linked customers.
DEFAULT_GIANT_COMMUNITY_FRAC = 0.2

# Communities this small are not interesting to Stage 5 (a single unconnected customer,
# or an isolated pair) -- keep them in the returned communities for completeness/audit
# but they're callable out separately in main()'s summary.
MIN_INTERESTING_SIZE = 3


@dataclass
class CommunityResult:
    communities: list[list[str]]
    method: str
    resolution: float
    giant_flags: list[dict] = field(default_factory=list)

    @property
    def n_communities(self) -> int:
        return len(self.communities)


def load_resolution(config_path: Path = CONFIG_PATH) -> float:
    """Read `community_resolution` from config.yaml. Fails loudly on the 0 TODO
    placeholder, same policy as graph/weights.py::load_edge_weights -- a silent 0
    would either degenerate Leiden's CPM objective or make Louvain's modularity
    resolution meaningless, producing garbage communities without any error."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    resolution = config.get("community_resolution")
    if resolution is None:
        raise ValueError("config.yaml missing `community_resolution`")
    if resolution == 0:
        raise ValueError(
            "config.yaml `community_resolution` is still at the placeholder 0 -- "
            "set a real value (e.g. 1.0) before running community detection."
        )
    return float(resolution)


def _customer_nodes(graph: nx.Graph) -> list[str]:
    return [n for n, d in graph.nodes(data=True) if d["node_type"] == "customer"]


def _detect_leiden(graph: nx.Graph, resolution: float) -> list[set[str]]:
    import igraph as ig
    import leidenalg

    nodes = list(graph.nodes())
    index_of = {n: i for i, n in enumerate(nodes)}
    edges = [(index_of[u], index_of[v]) for u, v in graph.edges()]
    weights = [graph.edges[u, v].get("weight", 1.0) for u, v in graph.edges()]

    ig_graph = ig.Graph(n=len(nodes), edges=edges)
    ig_graph.es["weight"] = weights

    partition = leidenalg.find_partition(
        ig_graph,
        leidenalg.CPMVertexPartition,
        weights="weight",
        resolution_parameter=resolution,
        seed=42,
    )

    communities = []
    for cluster_indices in partition:
        communities.append({nodes[i] for i in cluster_indices})
    return communities


def _detect_louvain(graph: nx.Graph, resolution: float) -> list[set[str]]:
    import community as community_louvain

    partition = community_louvain.best_partition(
        graph, weight="weight", resolution=resolution, random_state=42
    )
    grouped: dict[int, set[str]] = {}
    for node, community_id in partition.items():
        grouped.setdefault(community_id, set()).add(node)
    return list(grouped.values())


def detect_communities(
    graph: nx.Graph,
    resolution: float | None = None,
    method: str = "auto",
    giant_community_frac: float = DEFAULT_GIANT_COMMUNITY_FRAC,
) -> CommunityResult:
    """Run weighted community detection and return customer-only communities.

    `method`: "auto" (Leiden if leidenalg+igraph import cleanly, else Louvain),
    "leiden", or "louvain".
    """
    resolution = resolution if resolution is not None else load_resolution()

    used_method = method
    if method == "auto":
        try:
            import igraph  # noqa: F401
            import leidenalg  # noqa: F401

            used_method = "leiden"
        except ImportError:
            used_method = "louvain"

    if used_method == "leiden":
        raw_communities = _detect_leiden(graph, resolution)
    elif used_method == "louvain":
        raw_communities = _detect_louvain(graph, resolution)
    else:
        raise ValueError(f"unknown method: {method!r}")

    customer_set = set(_customer_nodes(graph))
    communities = []
    for raw in raw_communities:
        customers_only = sorted(raw & customer_set)
        if customers_only:
            communities.append(customers_only)

    # Sort largest-first so the giant-blob risk (if any) is immediately visible at the
    # top of any printed/inspected output.
    communities.sort(key=len, reverse=True)

    total_clustered = sum(len(c) for c in communities)
    giant_flags = flag_giant_communities(communities, total_clustered, giant_community_frac)

    return CommunityResult(
        communities=communities,
        method=used_method,
        resolution=resolution,
        giant_flags=giant_flags,
    )


def flag_giant_communities(
    communities: list[list[str]],
    total_clustered: int,
    giant_community_frac: float = DEFAULT_GIANT_COMMUNITY_FRAC,
) -> list[dict]:
    """Flag any community whose size exceeds `giant_community_frac` of all clustered
    customers. Doesn't raise or drop the community -- just returns flags for the caller
    to report, per the "don't crash, flag for tuning" requirement."""
    if total_clustered == 0:
        return []
    flags = []
    for idx, members in enumerate(communities):
        frac = len(members) / total_clustered
        if frac >= giant_community_frac:
            flags.append(
                {
                    "community_index": idx,
                    "size": len(members),
                    "fraction_of_clustered": frac,
                }
            )
    return flags


def community_summary(result: CommunityResult) -> dict:
    sizes = [len(c) for c in result.communities]
    size_hist = Counter(sizes)
    return {
        "method": result.method,
        "resolution": result.resolution,
        "n_communities": result.n_communities,
        "n_singletons": size_hist.get(1, 0),
        "largest_size": max(sizes) if sizes else 0,
        "median_size": sorted(sizes)[len(sizes) // 2] if sizes else 0,
        "n_giant_flags": len(result.giant_flags),
    }


def main() -> None:
    build_result = build_graph()
    graph = build_result.graph

    result = detect_communities(graph)
    summary = community_summary(result)

    print("=== Stage 4: community detection ===")
    print(f"Method: {summary['method']} (resolution={summary['resolution']})")
    print(f"Communities: {summary['n_communities']}")
    print(f"  singletons: {summary['n_singletons']}")
    print(f"  largest: {summary['largest_size']}")
    print(f"  median size: {summary['median_size']}")

    if result.giant_flags:
        warnings.warn(
            f"{len(result.giant_flags)} giant-blob community(ies) detected -- see "
            "printed detail below. This is a tuning note, not a crash.",
            stacklevel=2,
        )
        print("\n!!! GIANT-BLOB FLAG(S) -- tuning note, not a crash !!!")
        for flag in result.giant_flags:
            print(
                f"  community #{flag['community_index']}: {flag['size']} customers "
                f"({flag['fraction_of_clustered']:.1%} of all clustered customers)"
            )
        print(
            "  -> consider raising community_resolution in config.yaml, or dropping "
            "low-weight edge types (pincode/ip_subnet) before detection."
        )
    else:
        print("\nNo giant-blob communities flagged.")

    # Quick sanity signal against ground truth (debug-only, not used by detection
    # itself): how many communities of size >= MIN_INTERESTING_SIZE contain >1 ring.
    import pandas as pd

    customers = pd.read_csv(REPO_ROOT / "data" / "customers.csv", dtype=str)
    ring_by_customer = dict(zip(customers["customer_id"], customers["ring_id"].fillna("")))

    print(f"\nCommunities with >= {MIN_INTERESTING_SIZE} members, and their ring makeup:")
    interesting = [c for c in result.communities if len(c) >= MIN_INTERESTING_SIZE]
    print(f"  {len(interesting)} such communities (of {result.n_communities} total)")
    for members in interesting[:10]:
        ring_ids = [ring_by_customer.get(m, "") for m in members]
        ring_counts = Counter(r for r in ring_ids if r)
        tag = ", ".join(f"{rid}:{n}" for rid, n in ring_counts.most_common()) or "no ring members"
        print(f"    size={len(members):4d}  {tag}")


if __name__ == "__main__":
    main()
