"""Ad-hoc visual sanity check for Stage 3: render one ring and one benign family as
side-by-side subgraphs, edges colored/thickened by weight. Not part of the pipeline
(no `make` target) -- just a quick way to *see* the ring-vs-family contrast described
in docs/stage-3-graph-building.md.

Usage:
    .venv/bin/python -m graph.visualize
    .venv/bin/python -m graph.visualize --ring RING003 --family family_2
"""

from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import networkx as nx

from graph.build import DATA_DIR, _load_customers, build_graph

EDGE_COLORS = {
    "device": "#c0392b",
    "card": "#d35400",
    "phone": "#8e44ad",
    "address": "#2980b9",
    "ip_subnet": "#7f8c8d",
    "pincode": "#bdc3c7",
}


def _subgraph_for(graph: nx.Graph, customer_ids: list[str]) -> nx.Graph:
    nodes = set(customer_ids)
    for c in customer_ids:
        nodes.update(graph.neighbors(c))
    return graph.subgraph(nodes).copy()


def _draw(ax, graph: nx.Graph, sub: nx.Graph, title: str) -> None:
    pos = nx.spring_layout(sub, seed=42, k=0.9)

    node_colors = [
        "#2ecc71" if sub.nodes[n]["node_type"] == "customer" else "#34495e"
        for n in sub.nodes
    ]
    node_sizes = [
        450 if sub.nodes[n]["node_type"] == "customer" else 220 for n in sub.nodes
    ]

    for edge_type, color in EDGE_COLORS.items():
        edges = [
            (u, v) for u, v, d in sub.edges(data=True) if d["edge_type"] == edge_type
        ]
        if not edges:
            continue
        weight = sub.edges[edges[0]]["weight"]
        nx.draw_networkx_edges(
            sub, pos, edgelist=edges, edge_color=color, width=1 + weight * 4, ax=ax
        )

    nx.draw_networkx_nodes(sub, pos, node_color=node_colors, node_size=node_sizes, ax=ax)
    labels = {
        n: n.split(":")[-1] if ":" in n else n
        for n in sub.nodes
        if sub.nodes[n]["node_type"] == "customer"
    }
    nx.draw_networkx_labels(sub, pos, labels=labels, font_size=7, ax=ax)
    ax.set_title(title, fontsize=11)
    ax.axis("off")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ring", default=None, help="ring_id to render, e.g. RING001")
    parser.add_argument(
        "--family", default=None, help="cluster_tag to render, e.g. family_0"
    )
    parser.add_argument("--out", default="graph_preview.png")
    args = parser.parse_args()

    result = build_graph()
    graph = result.graph
    customers = _load_customers(DATA_DIR)

    ring_id = args.ring or sorted(
        customers[customers["ring_id"] != ""]["ring_id"].unique()
    )[0]
    family_tag = args.family or sorted(
        t for t in customers["cluster_tag"].unique() if t.startswith("family_")
    )[0]

    ring_members = customers[customers["ring_id"] == ring_id]["customer_id"].tolist()
    family_members = customers[customers["cluster_tag"] == family_tag][
        "customer_id"
    ].tolist()

    fig, axes = plt.subplots(1, 2, figsize=(14, 7))
    _draw(
        axes[0],
        graph,
        _subgraph_for(graph, ring_members),
        f"Ring {ring_id} ({len(ring_members)} members)",
    )
    _draw(
        axes[1],
        graph,
        _subgraph_for(graph, family_members),
        f"Family {family_tag} ({len(family_members)} members)",
    )

    handles = [
        plt.Line2D([0], [0], color=color, lw=3, label=edge_type)
        for edge_type, color in EDGE_COLORS.items()
    ]
    fig.legend(handles=handles, loc="lower center", ncol=6, fontsize=8)
    fig.suptitle("Green = customer, dark grey = shared attribute. Thicker/redder = higher weight.")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(args.out, dpi=150)
    print(f"Saved to {args.out}")


if __name__ == "__main__":
    main()
