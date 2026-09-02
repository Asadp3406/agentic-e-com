# Abuse Ring Sentinel

Detect coordinated fraud rings in e-commerce using a heterogeneous graph, community detection,
and an LLM agent investigator — with a React + FastAPI dashboard on top. Strictly defense-only.

Status: scaffolding only. No stages implemented yet. See [PLAN.md](PLAN.md) for the full design
and staged build plan.

## Overview

TODO: one paragraph — the problem (fraud rings vs. lone fraudsters), the approach (graph +
community detection + ring scoring + agent investigator), and the headline result once measured.

## How to run

### Engine (Part A)

```
make setup      # install dependencies
make data       # generate synthetic customers/orders/events + ground truth
make graph      # build the heterogeneous weighted graph
make detect     # community detection + ring-likelihood scoring
make investigate  # agent investigator produces case files per suspicious community
make eval       # metrics, ₹ cost sweep, baseline comparison
```

### API + Web (Part B — after the engine is done)

```
make api   # start the FastAPI backend
make web   # start the React dev server
```

### Everything

```
make demo
```

## Architecture

TODO: paste/adapt the ASCII diagrams from PLAN.md §4 (engine) and §B1 (web) once implemented.

## Metrics

TODO: ring-level and account-level precision/recall, ₹ false-positive cost, threshold sweep,
baseline (per-transaction classifier) vs. graph approach delta, and benign look-alike results.

## What broke and what I did

TODO: document real failures encountered per stage and how they were handled (see PLAN.md §10).

## Limitations (TODOs)

- [ ] Synthetic data only — no real transaction data.
- [ ] Threat-model scope: covers device/card/address/phone/IP sharing signals only.
- [ ] Defense-only — not intended for offensive use.
- [ ] GNN anomaly layer (Stage 6) is optional/advanced tier — status TBD.
- [ ] Stages 1–13 not yet implemented.
