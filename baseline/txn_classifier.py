"""Stage 4: per-transaction fraud classifier (structure-blind) used as the head-to-head baseline.

WHAT THIS IS FOR
-----------------
This is deliberately the *weak* approach: a classifier trained only on per-transaction
features (amount, promo usage, account age, order timing) with **zero** visibility into
the customer network -- no shared device/card/address, no community membership, nothing
from graph/ or detect/community.py. It represents what a typical rules/ML fraud stack
looks like *before* you add network analysis, and it exists so Stage 8's head-to-head
report has an honest, non-strawman baseline to beat.

WHY IT SHOULD (AND WILL) BE POOR AT CATCHING RINGS
----------------------------------------------------
Stage 1's synthetic rings are built to look ordinary at the transaction level: amounts
are drawn from the same distribution as legit orders, and most ring orders complete
without a chargeback/return/cod_refusal event (only a minority of ring orders carry an
elevated-but-not-saturating event rate -- see data/generate.py). What actually gives a
ring away is *coordination* -- many accounts sharing one device/card, created in a burst,
farming one promo code together -- and that signal lives in the graph, not in any single
transaction's amount or timing. A classifier that never sees the network literally cannot
learn "these 7 accounts are the same operator" from one row at a time. It can only learn
"transactions that look like past chargebacks/returns look risky," which catches some
scattered bad orders but has no way to connect them into a ring.

A NOTE ON WHY THE BASELINE ISN'T *TERRIBLE*
----------------------------------------------
Stage 1's synthetic rings do have elevated chargeback/return/cod_refusal rates (by
design, per data/generate.py) -- 92% of ring accounts have at least one bad event vs.
32% of legit accounts, and a ring account's per-order bad-event rate runs ~12x a legit
account's. So a transaction-level classifier isn't flying totally blind: it picks up
"this account's orders look risky" reasonably well. What it *can't* do is the thing that
actually matters operationally -- tell you *which other accounts are the same operator*,
or separate "genuinely elevated risk" from "innocent shared address/device" the way the
graph does (see the honesty traps in PLAN.md section 8). Its ring recall at a realistic
review budget (see below) is mediocre, and unlike the graph approach it has zero ability
to explain *why* an account is risky beyond "this order looks like past bad orders" --
no shared-entity evidence, no ring-vs-family distinction. That gap, not a rock-bottom
recall number, is the honest baseline story.

LABELS: WHAT WE PREDICT VS. WHAT WE EVALUATE AGAINST
-------------------------------------------------------
Training target (transaction-level, structure-blind, and the only labels a real
production system would have *before* investigating): whether an order has an associated
"bad" event in events.csv (chargeback, return, or cod_refusal). This is a legitimate,
observable target -- not cheating -- because these events are business outcomes, not
network structure.

Evaluation target (account-level, from ground_truth.csv, used only to score the
baseline -- never fed into training): whether the account belongs to an injected fraud
ring. An account is "flagged" using a **top-N% review budget**: rank all accounts by their
single riskiest order's predicted P(bad) (see `flag_accounts`), then flag the top
`review_budget_frac` of the customer base -- the same way a real fraud-ops team is
handed a finite review queue, not an arbitrary fixed probability cutoff (a fixed
threshold on this model's `class_weight="balanced"`-calibrated probabilities turned out
to flag either ~90% or ~0% of all accounts depending on the cutoff -- not a meaningful
operating point). We then compute ring recall/precision at that budget against ground
truth. This two-label setup is the whole point of the exercise: the model optimizes for
(and does okay on) the transaction-level target, but that's an imperfect proxy for the
account-level ring question -- it can rank individually-risky-looking accounts, but has
no way to tell "coordinated ring" apart from "one unlucky/dishonest individual," so ring
recall at any tight review budget is well below what the graph approach (Stage 5+)
should achieve at a comparable cost.

FEATURES (transaction-level only -- see `build_features`)
------------------------------------------------------------
  amount                      order amount
  log_amount                  log1p(amount), amounts are right-skewed
  used_promo                  whether a promo_code was applied
  account_age_days            days between customer created_at and this order's date
  order_hour, order_dow       time-of-day / day-of-week the order was placed
  customer_order_seq          1-indexed position of this order among the customer's
                               orders ordered by date (a brand-new account's 1st order
                               looks different from a 50th-order regular)
  days_since_prev_order       gap since this customer's previous order (inf/large for
                               the first order) -- transaction-level "burstiness" proxy,
                               deliberately NOT the same as the graph's cross-account
                               timing-burst feature Stage 5 will build
No device/card/address/community/ring features are used or even loaded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

FEATURE_COLUMNS = [
    "log_amount",
    "used_promo",
    "account_age_days",
    "order_hour",
    "order_dow",
    "customer_order_seq",
    "days_since_prev_order",
]

# Review-budget operating points to report: top N% of the customer base, ranked by
# risk score, flagged for review -- mirrors a real fraud-ops queue with finite capacity.
REVIEW_BUDGETS = [0.02, 0.05, 0.10]
DEFAULT_REVIEW_BUDGET = 0.05


@dataclass
class BaselineResult:
    model: LogisticRegression
    scaler: StandardScaler
    test_metrics: dict
    account_scores: pd.Series  # customer_id -> max predicted P(bad) across their orders
    budget_results: dict  # review_budget_frac -> {ring_recall, ring_precision, n_flagged, n_ring_accounts}


def _load_raw(data_dir: Path = DATA_DIR) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    orders = pd.read_csv(data_dir / "orders.csv", parse_dates=["order_date"])
    customers = pd.read_csv(data_dir / "customers.csv", parse_dates=["created_at"], dtype={"ring_id": str, "cluster_tag": str})
    events = pd.read_csv(data_dir / "events.csv")
    return orders, customers, events


def build_features(
    orders: pd.DataFrame, customers: pd.DataFrame, events: pd.DataFrame
) -> pd.DataFrame:
    """Build the transaction-level, network-blind feature table. One row per order."""
    df = orders.merge(
        customers[["customer_id", "created_at"]], on="customer_id", how="left"
    )

    df["log_amount"] = np.log1p(df["amount"])
    df["used_promo"] = df["promo_code"].notna().astype(int)
    df["account_age_days"] = (df["order_date"] - df["created_at"]).dt.total_seconds() / 86400.0
    df["order_hour"] = df["order_date"].dt.hour
    df["order_dow"] = df["order_date"].dt.dayofweek

    df = df.sort_values(["customer_id", "order_date"]).reset_index(drop=True)
    df["customer_order_seq"] = df.groupby("customer_id").cumcount() + 1
    prev_date = df.groupby("customer_id")["order_date"].shift(1)
    gap_days = (df["order_date"] - prev_date).dt.total_seconds() / 86400.0
    # First order per customer has no previous order -- fill with a large constant
    # (not 0, which would falsely say "just ordered again immediately").
    df["days_since_prev_order"] = gap_days.fillna(365.0)

    bad_order_ids = set(events["order_id"].unique())
    df["is_bad_event"] = df["order_id"].isin(bad_order_ids).astype(int)

    return df


def train_baseline(
    df: pd.DataFrame,
    test_size: float = 0.25,
    random_state: int = 42,
) -> tuple[LogisticRegression, StandardScaler, pd.DataFrame]:
    """Train logistic regression on transaction features to predict is_bad_event.
    Split is stratified on the label and done at the transaction level (not by
    customer) since the target and features are both transaction-level; account-level
    rollup happens downstream in flag_accounts."""
    X = df[FEATURE_COLUMNS].to_numpy(dtype=float)
    y = df["is_bad_event"].to_numpy(dtype=int)

    X_train, X_test, y_train, y_test, idx_train, idx_test = train_test_split(
        X, y, df.index, test_size=test_size, random_state=random_state, stratify=y
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    model = LogisticRegression(
        class_weight="balanced", max_iter=1000, random_state=random_state
    )
    model.fit(X_train_scaled, y_train)

    test_df = df.loc[idx_test].copy()
    test_df["y_true"] = y_test
    test_df["y_score"] = model.predict_proba(X_test_scaled)[:, 1]
    test_df["y_pred"] = (test_df["y_score"] >= 0.5).astype(int)

    return model, scaler, test_df


def transaction_metrics(test_df: pd.DataFrame) -> dict:
    y_true = test_df["y_true"]
    y_pred = test_df["y_pred"]
    y_score = test_df["y_score"]
    return {
        "n_test_orders": len(test_df),
        "n_bad_test_orders": int(y_true.sum()),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "f1": f1_score(y_true, y_pred, zero_division=0),
        "roc_auc": roc_auc_score(y_true, y_score) if y_true.nunique() > 1 else float("nan"),
    }


def score_all_orders(
    model: LogisticRegression, scaler: StandardScaler, df: pd.DataFrame
) -> pd.Series:
    """Score every order (train+test) with the fitted model, for account-level rollup.
    Using the full dataset here (not just the held-out test split) mirrors how this
    baseline would run in production -- scoring every order it sees -- while the
    reported transaction-level precision/recall above still comes only from the
    held-out test split."""
    X = df[FEATURE_COLUMNS].to_numpy(dtype=float)
    X_scaled = scaler.transform(X)
    return pd.Series(model.predict_proba(X_scaled)[:, 1], index=df.index)


def flag_accounts(df: pd.DataFrame, order_scores: pd.Series) -> pd.Series:
    """Roll up per-order P(bad) scores to a per-customer risk score: the single
    riskiest order a customer has placed. Returns customer_id -> max score; turning
    that into a yes/no flag is left to evaluate_ring_detection's top-N% budget cut,
    not a fixed probability threshold (see module docstring for why)."""
    scored = df.assign(score=order_scores)
    return scored.groupby("customer_id")["score"].max()


def evaluate_ring_detection(
    account_scores: pd.Series,
    ground_truth: pd.DataFrame,
    review_budget_frac: float = DEFAULT_REVIEW_BUDGET,
) -> tuple[float, float, int, int]:
    """Flag the top `review_budget_frac` of all accounts by risk score (a fixed review
    queue size, like a real fraud-ops team would be given), then compute:
    Ring recall = fraction of true ring accounts caught in that flagged set.
    Ring precision = fraction of the flagged set that's actually ring accounts.
    Both computed against ground_truth.csv's ring/legit labels -- evaluation only,
    never seen during training or used to pick the budget."""
    gt = ground_truth.set_index("account_id")["label"]
    is_ring = gt != "legit"

    scores = account_scores.reindex(gt.index, fill_value=0.0)
    n_flagged = max(1, round(len(gt) * review_budget_frac))
    flagged_ids = set(scores.sort_values(ascending=False).head(n_flagged).index)

    n_ring_accounts = int(is_ring.sum())
    n_ring_flagged = int(is_ring.reindex(flagged_ids).sum())

    ring_recall = n_ring_flagged / n_ring_accounts if n_ring_accounts else 0.0
    ring_precision = n_ring_flagged / n_flagged if n_flagged else 0.0

    return ring_recall, ring_precision, n_flagged, n_ring_accounts


def run_baseline(
    data_dir: Path = DATA_DIR,
    review_budgets: list[float] = REVIEW_BUDGETS,
) -> BaselineResult:
    orders, customers, events = _load_raw(data_dir)
    df = build_features(orders, customers, events)

    model, scaler, test_df = train_baseline(df)
    test_metrics = transaction_metrics(test_df)

    order_scores = score_all_orders(model, scaler, df)
    account_scores = flag_accounts(df, order_scores)

    ground_truth = pd.read_csv(data_dir / "ground_truth.csv", dtype=str)
    budget_results = {}
    for budget in review_budgets:
        ring_recall, ring_precision, n_flagged, n_ring_accounts = evaluate_ring_detection(
            account_scores, ground_truth, budget
        )
        budget_results[budget] = {
            "ring_recall": ring_recall,
            "ring_precision": ring_precision,
            "n_flagged": n_flagged,
            "n_ring_accounts": n_ring_accounts,
        }

    return BaselineResult(
        model=model,
        scaler=scaler,
        test_metrics=test_metrics,
        account_scores=account_scores,
        budget_results=budget_results,
    )


def main() -> None:
    result = run_baseline()

    print("=== Stage 4: baseline per-transaction classifier (network-blind) ===")
    print("Transaction-level metrics (held-out test split, target = has chargeback/"
          "return/cod_refusal event):")
    m = result.test_metrics
    print(f"  n_test_orders   {m['n_test_orders']}")
    print(f"  n_bad_test      {m['n_bad_test_orders']}")
    print(f"  precision       {m['precision']:.3f}")
    print(f"  recall          {m['recall']:.3f}")
    print(f"  f1              {m['f1']:.3f}")
    print(f"  roc_auc         {m['roc_auc']:.3f}")

    print("\nAccount-level ring detection at fixed review-budget operating points "
          "(top N% of accounts by risk score flagged for review), evaluated against "
          "ground_truth.csv -- NOT used in training:")
    n_ring = next(iter(result.budget_results.values()))["n_ring_accounts"]
    print(f"  ring accounts (ground truth): {n_ring}")
    for budget, metrics in sorted(result.budget_results.items()):
        print(
            f"  top {budget:>4.0%} reviewed ({metrics['n_flagged']:4d} accounts): "
            f"ring recall={metrics['ring_recall']:>6.1%}  "
            f"ring precision={metrics['ring_precision']:>6.1%}"
        )
    print("\nExpected: recall at a tight review budget should be mediocre -- this "
          "classifier has never seen a device, card, address, or community, so it can "
          "rank individually-suspicious-looking accounts but cannot detect "
          "*coordination*, i.e. it has no way to say 'these accounts are one operator.' "
          "That gap is what the graph-based approach (Stage 5+) is built to close.")


if __name__ == "__main__":
    main()
