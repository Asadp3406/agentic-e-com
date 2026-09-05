"""Stage 6 (advanced, optional): graph autoencoder anomaly layer.

WHAT THIS DOES
---------------
Stage 5's `scorer.py` is entirely hand-crafted features -- six numbers a human decided
mattered, combined with hand-picked weights. That's honest and explainable, but it can
only ever catch what those six features are looking for. This stage asks a different
question: can a model that *learns* a representation of "what a normal node's
neighborhood looks like" from the graph's structure alone catch coordination that the
hand-crafted features miss?

APPROACH: UNSUPERVISED GRAPH AUTOENCODER, NOT SUPERVISED CLASSIFICATION
--------------------------------------------------------------------------
We deliberately picked the unsupervised option (PLAN.md §Stage 6 offers either a
supervised GraphSAGE classifier on `ring_id`, or an unsupervised autoencoder) for the
same reason `scorer.py`'s weights aren't fit to `ring_id`: every other stage in this
project treats ground truth as eval-only, never as a training input, specifically so the
final numbers mean "this generalizes to structure we didn't tell it about" rather than
"this memorized the answer key." A supervised classifier trained directly on `ring_id`
would need a careful train/test split to keep that promise, and even then would be
learning "what THIS synthetic generator's rings look like" rather than "what anomalous
graph structure looks like" -- a narrower, more brittle signal. The autoencoder never
sees `ring_id`/`cluster_tag` at any point, training or scoring.

Concretely: a 2-layer GraphSAGE encoder learns to compress each node's local
neighborhood (its own attributes + a weighted sample of its neighbors' attributes) into
a small embedding, and a dot-product decoder is trained to reconstruct the graph's edges
from those embeddings (standard `torch_geometric.nn.GAE` link-reconstruction objective).
A node whose embedding reconstructs its neighborhood well is "structurally unsurprising"
-- it looks like most other nodes with that kind of neighborhood. A node whose embedding
reconstructs poorly is structurally anomalous relative to everything the model has seen.
We use each node's mean edge-reconstruction error (1 - predicted probability of its real
edges) as a per-node anomaly score, then aggregate to a per-community score for fusion
into `scorer.py`.

NODE FEATURES (what the GNN sees per node, before any message passing)
-------------------------------------------------------------------------
The graph is bipartite (customer nodes + device/card/phone/address/ip_subnet/pincode
entity nodes, per graph/build.py). Both node types get one shared feature vector so a
single GraphSAGE encoder can run over the whole graph:
  - degree (log1p) -- how many edges this node has.
  - weighted degree (log1p) -- sum of incident edge weights (a node with a few
    high-weight edges looks different from one with many low-weight edges).
  - per-edge-type degree share (6 values, one per edge_type: device/card/phone/address/
    ip_subnet/pincode) -- e.g. a customer whose edges are 100% "device" looks different
    from one that's evenly spread. Entity nodes trivially have 100% in their own type.
  - is_customer (0/1 flag) -- the two node types otherwise share the address space, so
    the model needs to know which side of the bipartition it's on.
  - customer-only behavioral features (0 for entity nodes): log1p(n_events),
    log1p(account_age_days at dataset-max created_at). These are NEVER ring_id/
    cluster_tag -- see graph/build.py's docstring on why those are off-limits.
None of this duplicates `features.py`'s community-level features directly; it's raw
per-node structural/behavioral signal for the GNN to combine on its own via message
passing, which is the point of using a GNN instead of just adding these as more
hand-crafted scorer.py features.

FUSION INTO scorer.py
-----------------------
`gnn_community_scores()` aggregates per-node anomaly scores to one value per community
(mean over the community's customer-node anomaly scores) and min-max normalizes across
communities into [0, 1], the same convention every other scorer.py sub-score uses.
scorer.py adds this as a 7th weighted feature, `gnn_anomaly`, gated by config.yaml's
`gnn.enabled` flag -- see scorer.py's module docstring for the fusion weight and the
before/after eval result.

CUT RULE (PLAN.md §13 / §Stage 6)
------------------------------------
"If this eats more than ~1.5 days, drop it and keep Stage 5. Say so in the README." See
docs/stage-6-gnn.md and README's Metrics section for whether the eval justified
keeping `gnn.enabled: true` or turning it off.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
CONFIG_PATH = REPO_ROOT / "config.yaml"

EDGE_TYPES = ["device", "card", "phone", "address", "ip_subnet", "pincode"]

# Fixed for reproducibility -- same spirit as community_resolution/random_seed elsewhere
# in this project: a GNN result that isn't reproducible run-to-run isn't a result.
RANDOM_SEED = 42
DEFAULT_EMBEDDING_DIM = 16
DEFAULT_HIDDEN_DIM = 32
DEFAULT_EPOCHS = 100
DEFAULT_LR = 0.01


DEFAULT_FUSION_WEIGHT = 0.15


@dataclass
class GnnConfig:
    enabled: bool
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    hidden_dim: int = DEFAULT_HIDDEN_DIM
    epochs: int = DEFAULT_EPOCHS
    lr: float = DEFAULT_LR
    fusion_weight: float = DEFAULT_FUSION_WEIGHT


def load_gnn_config(config_path: Path = CONFIG_PATH) -> GnnConfig:
    """Reads the `gnn:` block from config.yaml. Missing block or missing `enabled` key
    defaults to disabled -- this is an optional/advanced-tier feature per PLAN.md, so
    the absence of config should mean "off," not raise (unlike edge_weights/
    community_resolution, which are required for the core pipeline to mean anything)."""
    with open(config_path) as f:
        config = yaml.safe_load(f)
    block = config.get("gnn") or {}
    return GnnConfig(
        enabled=bool(block.get("enabled", False)),
        embedding_dim=int(block.get("embedding_dim", DEFAULT_EMBEDDING_DIM)),
        hidden_dim=int(block.get("hidden_dim", DEFAULT_HIDDEN_DIM)),
        epochs=int(block.get("epochs", DEFAULT_EPOCHS)),
        lr=float(block.get("lr", DEFAULT_LR)),
        fusion_weight=float(block.get("fusion_weight", DEFAULT_FUSION_WEIGHT)),
    )


# ---------------------------------------------------------------------------
# Node features
# ---------------------------------------------------------------------------


def build_node_features(graph: nx.Graph, data_dir: Path = DATA_DIR) -> tuple[list[str], np.ndarray]:
    """Returns (node_order, feature_matrix) -- node_order[i] is the node id whose
    feature row is feature_matrix[i]. Feature columns, in order:
      [log1p(degree), log1p(weighted_degree), share_device, share_card, share_phone,
       share_address, share_ip_subnet, share_pincode, is_customer,
       log1p(n_events), log1p(account_age_days)]
    Entity nodes get 0 for the two customer-only behavioral columns.
    """
    customers = pd.read_csv(data_dir / "customers.csv", dtype=str)
    max_created_at = pd.to_datetime(customers["created_at"]).max()

    nodes = list(graph.nodes())
    n_features = 1 + 1 + len(EDGE_TYPES) + 1 + 1 + 1
    matrix = np.zeros((len(nodes), n_features), dtype=np.float32)

    for i, node in enumerate(nodes):
        data = graph.nodes[node]
        neighbors = list(graph.neighbors(node))
        degree = len(neighbors)
        edge_type_weight = {t: 0.0 for t in EDGE_TYPES}
        weighted_degree = 0.0
        for nbr in neighbors:
            w = graph.edges[node, nbr].get("weight", 0.0)
            et = graph.edges[node, nbr].get("edge_type")
            weighted_degree += w
            if et in edge_type_weight:
                edge_type_weight[et] += w

        matrix[i, 0] = math.log1p(degree)
        matrix[i, 1] = math.log1p(weighted_degree)
        for j, t in enumerate(EDGE_TYPES):
            matrix[i, 2 + j] = (
                edge_type_weight[t] / weighted_degree if weighted_degree > 0 else 0.0
            )
        is_customer = data.get("node_type") == "customer"
        matrix[i, 2 + len(EDGE_TYPES)] = 1.0 if is_customer else 0.0

        if is_customer:
            n_events = data.get("n_events", 0)
            created_at = pd.Timestamp(data.get("created_at"))
            age_days = max((max_created_at - created_at).total_seconds() / 86400.0, 0.0)
            matrix[i, 3 + len(EDGE_TYPES)] = math.log1p(n_events)
            matrix[i, 4 + len(EDGE_TYPES)] = math.log1p(age_days)

    return nodes, matrix


# ---------------------------------------------------------------------------
# Model + training
# ---------------------------------------------------------------------------


def _build_pyg_data(graph: nx.Graph, node_order: list[str], features: np.ndarray):
    import torch
    from torch_geometric.data import Data

    index_of = {n: i for i, n in enumerate(node_order)}
    edges = [(index_of[u], index_of[v]) for u, v in graph.edges()]
    # Undirected graph -> add both directions so message passing sees symmetric edges.
    src = [e[0] for e in edges] + [e[1] for e in edges]
    dst = [e[1] for e in edges] + [e[0] for e in edges]
    edge_index = torch.tensor([src, dst], dtype=torch.long)
    x = torch.tensor(features, dtype=torch.float32)
    return Data(x=x, edge_index=edge_index)


def _make_encoder(in_channels: int, hidden_channels: int, out_channels: int):
    import torch.nn.functional as F
    from torch import nn
    from torch_geometric.nn import SAGEConv

    class SAGEEncoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.conv1 = SAGEConv(in_channels, hidden_channels)
            self.conv2 = SAGEConv(hidden_channels, out_channels)

        def forward(self, x, edge_index):
            x = F.relu(self.conv1(x, edge_index))
            x = self.conv2(x, edge_index)
            return x

    return SAGEEncoder()


@dataclass
class GnnTrainResult:
    node_order: list[str]
    embeddings: np.ndarray  # (n_nodes, embedding_dim)
    node_anomaly_score: dict[str, float]  # node_id -> reconstruction-error-based score, raw
    final_loss: float


def train_gae(
    graph: nx.Graph,
    cfg: GnnConfig | None = None,
    data_dir: Path = DATA_DIR,
) -> GnnTrainResult:
    """Trains a GraphSAGE-encoder graph autoencoder unsupervised (link-reconstruction
    objective only -- no ring_id/cluster_tag anywhere in this function) and returns
    per-node embeddings plus a per-node anomaly score (higher = harder for the model to
    reconstruct this node's edges from its embedding = more structurally unusual).
    """
    import torch
    from torch_geometric.nn import GAE
    from torch_geometric.utils import negative_sampling

    cfg = cfg or load_gnn_config()

    # All three RNG sources need seeding, not just torch's: PyG's negative_sampling()
    # falls back to Python's stdlib `random.sample` in its "fast path" (see
    # torch_geometric/utils/_negative_sampling.py), which torch.manual_seed does NOT
    # cover -- found by observing two back-to-back train_gae() calls in the same
    # process (both already torch.manual_seed'd) produce different negative-sampling
    # draws and thus different final losses. Also force single-threaded + deterministic
    # kernels, since PyTorch's default multi-threaded CPU reductions have non-fixed
    # summation order that compounds tiny float differences over `epochs` steps. This
    # matches the rest of the project's "same seed -> byte-identical output" policy
    # (config.yaml's random_seed, data/generate.py, etc.) -- verified by running
    # train_gae() twice in-process AND via two separate `python -m detect.gnn`
    # invocations and diffing final_loss to 6 decimal places.
    import random

    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)
    torch.manual_seed(RANDOM_SEED)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)

    node_order, features = build_node_features(graph, data_dir=data_dir)
    data = _build_pyg_data(graph, node_order, features)

    encoder = _make_encoder(features.shape[1], cfg.hidden_dim, cfg.embedding_dim)
    model = GAE(encoder)
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg.lr)

    final_loss = 0.0
    for _ in range(cfg.epochs):
        model.train()
        optimizer.zero_grad()
        z = model.encode(data.x, data.edge_index)
        neg_edge_index = negative_sampling(
            edge_index=data.edge_index,
            num_nodes=data.num_nodes,
            num_neg_samples=data.edge_index.size(1),
        )
        loss = model.recon_loss(z, data.edge_index, neg_edge_index)
        loss.backward()
        optimizer.step()
        final_loss = float(loss.item())

    model.eval()
    with torch.no_grad():
        z = model.encode(data.x, data.edge_index)
        edge_probs = model.decoder(z, data.edge_index, sigmoid=True)  # P(edge is real)

    embeddings = z.numpy()

    # Per-node anomaly score: mean (1 - reconstruction probability) over that node's
    # incident real edges. A node whose real edges the decoder is confident about
    # (prob near 1) is unsurprising; one whose edges the decoder can't reconstruct well
    # (prob near 0, i.e. its embedding doesn't predict its own neighborhood) is
    # anomalous. Using the node's OWN edges (not random pairs) keeps this a measure of
    # "how well does this node's structure fit the model," not general edge sparsity.
    src_nodes = data.edge_index[0].numpy()
    error = 1.0 - edge_probs.numpy()
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    for idx, e in zip(src_nodes, error):
        sums[idx] = sums.get(idx, 0.0) + float(e)
        counts[idx] = counts.get(idx, 0) + 1

    node_anomaly_score = {
        node_order[i]: (sums[i] / counts[i] if i in counts else 0.0)
        for i in range(len(node_order))
    }

    return GnnTrainResult(
        node_order=node_order,
        embeddings=embeddings,
        node_anomaly_score=node_anomaly_score,
        final_loss=final_loss,
    )


# ---------------------------------------------------------------------------
# Per-community aggregation (the signal scorer.py actually consumes)
# ---------------------------------------------------------------------------


def gnn_community_scores(
    communities: list[list[str]],
    train_result: GnnTrainResult,
) -> dict[int, float]:
    """Aggregate per-node anomaly scores to one raw value per community (mean over the
    community's customer members), then min-max normalize across all communities into
    [0, 1] -- same convention scorer.py's other sub-scores use, so it can be fused with
    a fixed weight like the hand-crafted six. Returns {community_index: normalized_score}.
    """
    raw = {}
    for idx, members in enumerate(communities):
        node_scores = [
            train_result.node_anomaly_score[m]
            for m in members
            if m in train_result.node_anomaly_score
        ]
        raw[idx] = float(np.mean(node_scores)) if node_scores else 0.0

    values = list(raw.values())
    lo, hi = min(values), max(values)
    spread = hi - lo
    if spread <= 0:
        return {idx: 0.0 for idx in raw}
    return {idx: (v - lo) / spread for idx, v in raw.items()}


def main() -> None:
    from graph.build import build_graph
    from detect.community import detect_communities

    cfg = load_gnn_config()
    print("=== Stage 6: graph autoencoder anomaly layer ===")
    print(f"gnn.enabled = {cfg.enabled} (config.yaml)")

    build_result = build_graph()
    graph = build_result.graph
    community_result = detect_communities(graph)

    print(
        f"Training GAE: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, "
        f"embedding_dim={cfg.embedding_dim}, hidden_dim={cfg.hidden_dim}, epochs={cfg.epochs}"
    )
    train_result = train_gae(graph, cfg)
    print(f"Final reconstruction loss: {train_result.final_loss:.4f}")

    community_scores = gnn_community_scores(community_result.communities, train_result)

    reportable = [
        (idx, community_result.communities[idx], score)
        for idx, score in community_scores.items()
        if len(community_result.communities[idx]) >= 3
    ]
    reportable.sort(key=lambda r: r[2], reverse=True)

    customers = pd.read_csv(DATA_DIR / "customers.csv", dtype=str)
    ring_by_customer = dict(zip(customers["customer_id"], customers["ring_id"].fillna("")))

    print(f"\nTop 15 communities (size >= 3) by GNN anomaly score:")
    print(f"{'rank':>4} {'gnn_score':>9} {'size':>5}  ring makeup")
    for rank, (idx, members, score) in enumerate(reportable[:15], start=1):
        from collections import Counter

        ring_counts = Counter(ring_by_customer.get(m, "") for m in members if ring_by_customer.get(m, ""))
        tag = ", ".join(f"{rid}:{n}" for rid, n in ring_counts.most_common()) or "no ring members"
        print(f"{rank:>4} {score:>9.3f} {len(members):>5}  {tag}")


if __name__ == "__main__":
    main()
