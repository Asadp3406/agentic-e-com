# Abuse Ring Sentinel — Project Plan (Advanced)

> Track 02 — AI Risk Manager.
> Detect coordinated **fraud rings** (not lone fraudsters) in e-commerce using a graph of
> shared identity signals + community detection, an optional GNN anomaly layer, and an
> **agent investigator** that gathers evidence, reasons about ring-vs-benign, and takes a
> bounded action per ring — with honest precision/recall and false-positive cost in ₹.
> Strictly defense-only.

---

## 0. Start from zero — what problem are we solving (plain language)

A single fraudster is easy-ish to catch. The expensive damage comes from **rings** — a group
of accounts run by the same operator(s) that *look* independent but are secretly connected,
and together they systematically:
- abuse returns (order, keep, claim "not received"),
- file chargebacks in bulk,
- refuse COD deliveries (costing the merchant reverse shipping),
- farm promo codes / referral bonuses / first-order discounts with fake accounts.

**Why normal fraud models miss them:** a per-transaction classifier looks at ONE order at a
time. Each order in a ring looks normal on its own. The fraud is only visible in the
**connections** — 8 "different" customers all using the same device, shipping to 3 near-identical
addresses, paying with cards issued to the same 2 people, all filing chargebacks the same week.

**The core idea:** stop looking at transactions one by one. Build a **graph** (a web of who is
connected to whom, through what), find tightly-connected **clusters**, and judge the *cluster*,
not the transaction. That's the whole project.

**The hard part (and why it's advanced):** not every cluster is a fraud ring. A family shares
one address. An office shares one IP. A hostel has 40 people at one pin code. A shared laptop
in a household links two accounts. If you flag every shared attribute, you'll block tons of
innocent people — that's the **false-positive trap**, and handling it well is what separates an
advanced project from a toy.

---

## 1. What makes this an ADVANCED project (not basic)

A basic version = "same address → flag." We are doing:
1. **Heterogeneous graph** — many node types (customer, device, address, card, phone, IP) and
   typed edges, not a single-type graph.
2. **Fuzzy entity resolution** — "same address" is rarely an exact string match (typos, "Apt"
   vs "Apartment", added landmarks). Device/phone/IP need normalization + similarity, not `==`.
3. **Community detection** — Louvain/Leiden modularity to find clusters, plus weighted edges so
   a shared *device* counts more than a shared *pin code*.
4. **Ring-likelihood scoring** — features that separate malicious coordination from benign
   co-location (behavioral correlation, timing bursts, chargeback density, attribute-sharing
   depth), not just cluster size.
5. **Optional GNN anomaly layer** (advanced tier) — node/community embeddings (GraphSAGE or a
   graph autoencoder) to catch rings that hand-crafted rules miss.
6. **Agent investigator** — an LLM agent that, per suspicious community, pulls the evidence
   subgraph, reasons "ring or benign?", and picks a bounded action with a written case file.
7. **Cost-sensitive decisions** — a false positive = a blocked real customer = lost revenue +
   reputation. We report ₹ cost and pick thresholds by money, not F1.
8. **Baseline comparison** — we build a per-transaction classifier too, and show the graph
   approach catches rings it structurally cannot. That head-to-head is the punchline.

---

## 2. Success criteria (what "done" means)

| # | Criterion | Target |
|---|-----------|--------|
| 1 | Runs end-to-end from README | `make demo` works cold |
| 2 | Builds a heterogeneous graph with fuzzy entity resolution | typed nodes/edges |
| 3 | Detects communities and scores ring-likelihood | ranked suspicious clusters |
| 4 | Agent produces an evidence-backed decision per ring | case file + bounded action |
| 5 | Honest metrics on held-out set | ring-level + account-level precision/recall |
| 6 | False-positive cost in ₹ + threshold sweep by money | chart |
| 7 | Beats a per-transaction baseline on ring recall | measured delta |
| 8 | Benign look-alike clusters NOT flagged | family/office/hostel pass |
| 9 | One failure handled gracefully | documented |
| 10 | 5-min video | script in §12 |

**Honesty rule:** you MUST include benign look-alike clusters in the test set and report your
false positives on them. A model that flags every shared-address cluster is worthless; showing
you *don't* is the credibility.

---

## 3. Domain primer — the signals a ring shares (read once)

Nodes (entities) and the edges (relationships) that connect them:

```
   CUSTOMER ──uses──▶ DEVICE            (device fingerprint / cookie id)
   CUSTOMER ──ships─▶ ADDRESS           (delivery address, fuzzy-matched)
   CUSTOMER ──pays──▶ CARD/UPI          (payment instrument / VPA)
   CUSTOMER ──has───▶ PHONE             (normalized number)
   CUSTOMER ──from──▶ IP / IP-SUBNET    (session IP)
```

Two customers are *indirectly connected* if they share any of these. A **ring** is a group where
many such shared links pile up AND the group behaves in a coordinated, loss-causing way.

**Edge weights matter:** sharing a device fingerprint is strong evidence (rare by accident);
sharing a /24 IP subnet or a city pin code is weak (millions share those). Weighting edges by
how *discriminating* the shared attribute is = a big part of avoiding false positives.

**Behavioral signals that turn "connected" into "ring":**
- chargeback / return / COD-refusal density within the cluster far above baseline,
- timing bursts (many accounts act within a tight window),
- account-age pattern (many fresh accounts created together),
- promo/refund concentration (same discount farmed repeatedly).

---

## 4. Architecture

```
  ┌──────────────────────┐
  │ data/ synthetic       │  customers, orders, devices, addresses, cards, phones, IPs,
  │  + ground_truth       │  chargebacks/returns/COD; labels: which accounts = which ring
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐   fuzzy match: addresses, phones, devices, IP-subnets
  │  ENTITY RESOLUTION    │──▶ canonical entity ids
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐   nodes: customer/device/address/card/phone/ip
  │  GRAPH BUILDER        │   edges: typed + weighted by discriminativeness
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐        ┌───────────────────────────┐
  │ COMMUNITY DETECTION   │        │  BASELINE (per-txn         │
  │ Louvain/Leiden        │        │  classifier) — for the     │
  │ (weighted)            │        │  head-to-head comparison   │
  └──────────┬───────────┘        └───────────────────────────┘
             ▼
  ┌──────────────────────┐   features: sharing depth, chargeback density, timing burst,
  │ RING-LIKELIHOOD SCORER│   account-age pattern, edge-weight mass, GNN embedding (opt)
  └──────────┬───────────┘
             ▼
  ┌──────────────────────────────────────────────┐
  │  AGENT INVESTIGATOR (LLM)                       │
  │  per suspicious community:                      │
  │   1 pull evidence subgraph (tools over graph)   │
  │   2 reason: ring vs benign co-location          │
  │   3 bounded action: monitor/hold/review/block   │
  │   4 write an auditable case file + confidence   │
  └──────────┬─────────────────────────────────────┘
             ▼
  ┌──────────────────────────────────────────────┐
  │  METRICS + REPORT                               │
  │  ring & account precision/recall, ₹ FP-cost,    │
  │  threshold sweep, baseline delta, case files    │
  └──────────────────────────────────────────────┘
```

Everything runs locally on synthetic data — no PII, fully reproducible.

---

## 5. Tech stack

- **Python 3.11**, **pandas**.
- **networkx** for the graph; **python-louvain** (community) or **igraph + leidenalg** for
  Leiden (better than Louvain). Pick one; Leiden if you want the advanced flavor.
- **rapidfuzz** for fuzzy address/name matching; **phonenumbers** for phone normalization.
- **scikit-learn** for the baseline per-transaction classifier + all metrics.
- **PyTorch Geometric** (advanced tier) for a GraphSAGE node classifier or graph autoencoder.
  Cut this tier if time is short — the project stands without it.
- **Claude (anthropic SDK)** for the agent investigator (tool-use loop over the graph).
- **matplotlib + networkx** to render evidence subgraphs (the visual proof).
- **Makefile**: setup, data, graph, detect, investigate, eval, demo.
- **config.yaml**: edge weights, community resolution, score thresholds, ₹ cost params.

---

## 6. Repo structure

```
abuse-ring-sentinel/
├── README.md
├── PLAN.md                       # this file
├── Makefile
├── .env.example                  # ANTHROPIC_API_KEY
├── config.yaml                   # edge weights, thresholds, cost model
├── data/
│   ├── generate.py               # synthetic population + rings + ground truth (the crux)
│   ├── customers.csv, orders.csv, devices.csv, addresses.csv,
│   ├── cards.csv, phones.csv, ips.csv, events.csv   (chargeback/return/COD)
│   └── ground_truth.csv          # account_id -> ring_id (or "legit")
├── resolve/
│   └── entity_resolution.py      # fuzzy match -> canonical entity ids
├── graph/
│   ├── build.py                  # heterogeneous weighted graph
│   └── weights.py                # edge weight by discriminativeness
├── detect/
│   ├── community.py              # Louvain/Leiden weighted community detection
│   ├── features.py               # per-community ring-likelihood features
│   ├── scorer.py                 # score + rank communities
│   └── gnn.py                    # (advanced tier) GraphSAGE / autoencoder anomaly
├── baseline/
│   └── txn_classifier.py         # per-transaction fraud model for head-to-head
├── agent/
│   ├── tools.py                  # graph query tools the agent can call
│   ├── investigator.py           # LLM tool-use loop: evidence -> verdict -> action
│   └── policy.py                 # bounded action + cost-aware thresholds
├── eval/
│   ├── run_eval.py               # ring/account P/R, ₹ FP-cost, sweep, baseline delta
│   └── report.md
└── demo/
    └── run_demo.py               # full pipeline + evidence subgraph render
```

---

## 7. Staged build plan

> In order. Each stage ends with something that RUNS. Advanced tiers are marked — cut them
> first if time is short; the project is still strong without them.

### Stage 1 — Synthetic population + ring generator + ground truth (2 days) ← the crux
**Goal:** realistic data where SOME clusters are rings and SOME are benign look-alikes.
- [ ] Generate ~1,000–3,000 legit customers with **realistic benign sharing**:
  - families sharing an address + occasionally a device,
  - offices/hostels sharing an IP subnet or pin code,
  - a couple sharing one card.
- [ ] Generate orders + events (returns, chargebacks, COD outcomes) at realistic base rates.
- [ ] Inject ~10–20 **fraud rings** with coordinated signatures:
  - shared device/card/address across "different" accounts,
  - elevated chargeback/return/COD-refusal density,
  - timing bursts + fresh-account clusters + promo farming.
- [ ] Write `ground_truth.csv`: account_id → ring_id or "legit".
- [ ] **Honesty traps (must include):** a large family, an office IP cluster, a hostel pin-code
  cluster — connected but NOT fraudulent. These are your false-positive tests.
- **Exit test:** print stats — #legit, #rings, ring sizes, and confirm benign look-alikes exist.

> Document the generation logic in the module docstring so the metrics are trustworthy.
> Fixed seed; hold out a slice you don't look at while tuning.

### Stage 2 — Entity resolution (fuzzy matching) (1 day)
**Goal:** turn messy attributes into canonical entity ids so real shares are detected.
- [ ] `entity_resolution.py`: normalize + fuzzy-match addresses (rapidfuzz), phones
  (phonenumbers), device ids, and bucket IPs into subnets.
- [ ] Output canonical ids so "Apt 4B, MG Road" and "Apartment 4-B MG Rd" map together.
- **Exit test:** near-duplicate addresses collapse correctly; distinct ones stay separate
  (check precision of matching on a few hand cases).

### Stage 3 — Heterogeneous weighted graph (1 day)
**Goal:** build the graph with edge weights by how discriminating each shared attribute is.
- [ ] `weights.py`: weight = f(rarity of the shared attribute). Device/card = high; pin/IP-subnet = low.
- [ ] `graph/build.py`: nodes for each entity type, typed weighted edges.
- **Exit test:** spot-check a known ring — its accounts are densely linked via high-weight edges;
  a benign family is linked but via fewer/lower-weight edges.

### Stage 4 — Community detection + baseline classifier (1 day)
**Goal:** find clusters, and build the baseline you'll beat.
- [ ] `community.py`: weighted Louvain/Leiden → communities.
- [ ] `baseline/txn_classifier.py`: a per-transaction fraud classifier (logistic/GBM) on
  transaction features only — deliberately blind to network structure.
- **Exit test:** communities returned; baseline trained + scored. Note baseline's ring recall
  (it will be poor — that's the point).

### Stage 5 — Ring-likelihood scoring (1 day) ← core signal
**Goal:** separate malicious rings from benign clusters.
- [ ] `features.py`: per-community features — high-weight-edge mass, chargeback/return/COD
  density vs baseline, timing-burst score, fresh-account ratio, promo concentration, size.
- [ ] `scorer.py`: combine into a ring-likelihood score; rank communities.
- **Exit test:** injected rings rank high; family/office/hostel clusters rank low. If a benign
  cluster ranks high, inspect which feature betrayed you and fix it — this IS the project.

### Stage 6 (ADVANCED tier) — GNN anomaly layer (1–1.5 days, optional)
**Goal:** catch rings the hand-crafted features miss, via learned embeddings.
- [ ] `gnn.py`: GraphSAGE node classifier (supervised on ground truth) OR a graph autoencoder
  (unsupervised anomaly via reconstruction error). Feed embeddings into the scorer.
- **Exit test:** GNN improves ring recall at equal false-positive rate over Stage 5 alone.
- **Cut rule:** if this eats more than ~1.5 days, drop it and keep Stage 5. Say so in README.

### Stage 7 — Agent investigator (tool-use loop) (1.5 days) ← the "agent"
**Goal:** an LLM agent that investigates each suspicious community and decides an action.
- [ ] `agent/tools.py`: graph-query tools the agent can call — get_members(community),
  get_shared_entities, get_events(members), get_timing, compare_to_baseline. (These make it a
  real tool-use agent, not a one-shot prompt.)
- [ ] `agent/investigator.py`: loop — the agent calls tools to gather evidence, reasons about
  **ring vs benign co-location** (must actively rule out family/office/hostel), and emits a
  structured verdict: {is_ring, confidence, evidence[], recommended_action, reasoning}.
- [ ] `agent/policy.py`: bounded actions — monitor / hold / manual-review / block — chosen with
  the cost model (never auto-block below a confidence tied to ₹ risk). No action without cited
  evidence; write an auditable **case file** per community.
- **Exit test:** on a real ring → correct verdict + evidence + action; on a benign family →
  agent explicitly clears it ("shared address but no elevated chargebacks, no timing burst").

### Stage 8 — Metrics + baseline delta + cost (1 day)
**Goal:** prove it honestly.
- [ ] `eval/run_eval.py`: ring-level and account-level precision/recall/F1 vs ground truth.
- [ ] ₹ cost model: fraud caught (₹ saved) vs legit blocked (₹ lost); threshold sweep; pick
  operating point by net ₹.
- [ ] **Head-to-head:** graph approach vs baseline classifier on ring recall — the punchline.
- [ ] Report false positives on the benign look-alike clusters explicitly.
- **Exit test:** `make eval` → all metrics + the baseline-delta chart + the ₹ sweep.

### Stage 9 — Failure handling + demo + README (1 day)
See §10, §11, §12.

---

## 8. Honesty traps to include (your credibility lives here)
- **Large family:** 5 accounts, one address, one shared tablet — no elevated chargebacks. Must
  NOT be flagged.
- **Office / co-working IP:** 30 accounts on one IP subnet — normal behavior. Must NOT be flagged.
- **Hostel / PG pin code:** many accounts, one pin — must NOT be flagged on location alone.
- **Ambiguous cluster:** shares a device AND has slightly elevated returns but no chargebacks —
  the agent should `hold`/`review`, not `block`. Tests calibrated action.
Report your performance on each in the eval. This is what makes judges trust the numbers.

## 9. The "wow" in the demo
1. Show a ring as a rendered **evidence subgraph** — 7 "different" customers visibly linked
   through one device + two near-identical addresses + shared cards, with 5 chargebacks in 6 days.
2. Show the baseline classifier **missing** it (each transaction looked fine).
3. Show the agent's **case file**: evidence gathered, ring-vs-benign reasoning, bounded action,
   confidence.
4. Show a benign family that shares an address getting **cleared** — no false alarm.
The contrast (graph catches what per-transaction misses; and it doesn't cry wolf) is the story.

## 10. Failure handling (graded)
- **Bad/missing attribute rows** → entity resolution isolates them, pipeline continues.
- **LLM malformed JSON / timeout** in the investigator → one repair retry, else fall back to the
  score-only verdict marked `degraded`.
- **Agent over-reach guard** → it cannot recommend `block` without citing concrete evidence
  above the cost-tied confidence; otherwise it must downgrade to `review`.
- **Huge community** (whole-graph blob from a bad weight) → cap + flag for tuning, don't hang.
Write as "what broke and what I did."

## 11. README must include
One-command run; architecture (ASCII §4); the pitch (§0); metrics + baseline-delta chart + ₹
sweep; the benign-cluster results (false positives you own); "what broke and what I did";
limitations (synthetic data, threat-model scope, defense-only).

## 12. 5-minute video script (beats)
- **0:00–0:40** Problem: rings, not lone actors. Per-transaction models can't see them because
  the fraud lives in the connections.
- **0:40–1:20** Approach: heterogeneous graph → communities → ring-likelihood → agent decides.
  Diagram.
- **1:20–2:20** The evidence subgraph of a real ring; baseline classifier misses it.
- **2:20–3:20** Agent investigator case file: evidence, ring-vs-benign reasoning, bounded action.
- **3:20–4:00** A benign family that shares an address gets CLEARED — no false alarm.
- **4:00–4:40** Metrics: ring recall vs baseline + ₹ false-positive cost + threshold-by-money.
- **4:40–5:00** One failure handled + what's next (streaming detection, GNN tier).

## 13. Scope discipline (cut in this order if short)
1. Cut Stage 6 (GNN) first — Stage 5 features carry the project.
2. Cut the streaming/online idea (keep batch).
3. Simplify entity resolution to address+device+card (drop phone/IP) if needed.
Keep no matter what: heterogeneous graph, community detection, ring scoring, the agent
investigator with case files, the baseline head-to-head, ₹ cost, and the benign-cluster tests.

## 14. Day-by-day (~9–10 focused days; compress by cutting Stage 6 + trimming Stage 1)
- Day 1–2: Stage 1 (data + rings + ground truth + benign traps).
- Day 3: Stage 2 (entity resolution).
- Day 4: Stage 3 (graph).
- Day 5: Stage 4 (community + baseline).
- Day 6: Stage 5 (ring scoring).
- Day 7: Stage 6 (GNN) — or skip and buffer.
- Day 8: Stage 7 (agent investigator).
- Day 9: Stage 8 (metrics).
- Day 10: Stage 9 (failures, demo, README, record).
```

---

# PART B — Web App Layer (React + FastAPI)

> Build the detection engine (Part A, Stages 1–9) FIRST. This UI sits on top and calls it.
> A great-looking graph of a fraud ring is the single most impressive thing in the demo, so
> the frontend earns its keep — but only once the engine actually detects rings.

## B0. What the web app is (plain language)
A browser dashboard where you press "Run detection," watch the customer network draw itself as
an interactive graph, see fraud rings light up red while benign clusters stay green, click a
ring to open the agent's investigation case file, and view the metrics vs the baseline. It
turns a backend pipeline into something you can *see and explore*.

## B1. Web architecture

```
  ┌────────────────────────────────────────────┐
  │  REACT FRONTEND (browser)                     │
  │  • Network graph (react-force-graph)          │
  │  • Ring dashboard (summary cards + ranked list)│
  │  • Case-file panel (agent evidence + action)  │
  │  • Metrics view (charts, baseline delta)      │
  └───────────────┬────────────────────────────┘
                  │ REST (JSON)
                  ▼
  ┌────────────────────────────────────────────┐
  │  FASTAPI BACKEND                              │
  │  wraps the Part-A engine as API endpoints     │
  └───────────────┬────────────────────────────┘
                  ▼
  ┌────────────────────────────────────────────┐
  │  ENGINE (Part A): resolve → graph → detect →  │
  │  score → agent investigator → metrics         │
  └────────────────────────────────────────────┘
```

The engine stays exactly as planned; FastAPI just exposes it. The frontend never does
detection logic — it only renders what the API returns.

## B2. Frontend stack
- **React** (Vite) + **TypeScript**.
- **react-force-graph** (2D) for the interactive network — nodes draggable, zoom/pan, click
  events. (Cytoscape.js is the alternative if you want more layout control.)
- **Recharts** for metrics/₹-cost/baseline-delta charts.
- **Tailwind CSS** for a clean, modern look with minimal effort.
- **TanStack Query** (or plain fetch) for API calls + loading states.

## B3. Backend API endpoints (FastAPI)
Expose the engine as a small, clean API:
- `POST /api/run` — run the full pipeline on the loaded dataset; returns a run id.
- `GET  /api/graph` — nodes + edges for the network view (typed, with ring/benign labels,
  edge weights, and a `suspicion` flag per node/cluster for coloring).
- `GET  /api/rings` — ranked suspicious communities: id, size, score, ₹ risk, status.
- `GET  /api/rings/{id}` — the case file: members, shared entities, evidence, agent reasoning,
  recommended action, confidence, and the evidence-subgraph (nodes+edges subset).
- `GET  /api/metrics` — ring/account precision-recall, ₹ FP-cost curve, baseline-vs-graph delta.
- `GET  /api/benign` — the benign look-alike clusters and how they were (correctly) cleared.
Keep payloads render-ready (pre-shaped for the graph lib) so the frontend stays dumb.

## B4. Frontend screens (what to build)
1. **Overview / Run bar** — a "Run detection" button; on click, show progress, then summary
   cards: #rings found, #accounts flagged, total ₹ at risk, false-positive rate on benign set.
2. **Network Graph (hero screen)** — the whole customer network. Rings in red, benign clusters
   green, node size by risk. Hover = tooltip; click a node = highlight its cluster + open panel.
   A filter to "show rings only" and a legend.
3. **Ring List** — ranked table of suspicious clusters (score, size, ₹ risk, status chip).
   Click a row → focuses that cluster in the graph AND opens the case file.
4. **Case-File Panel (slide-over)** — the agent's investigation: the evidence subgraph rendered
   small, the shared entities (device/address/card), the behavioral evidence (chargeback burst,
   timing), the agent's ring-vs-benign reasoning, recommended bounded action, and confidence.
5. **Metrics View** — Recharts: precision/recall, the ₹ false-positive-cost threshold sweep,
   and the graph-vs-baseline bar (the punchline). Plus the benign-cluster results (owned FPs).

## B5. Making it attractive (design notes)
- Dark theme, generous spacing, one accent color for danger (red) and one for safe (green).
- Animate the graph settling into place on load — the "network forming" effect reads as magic.
- Color edges by weight (thicker/brighter = more discriminating share like device/card).
- When a ring is selected, dim the rest of the graph so the ring pops.
- Show the agent's reasoning as a readable narrative, not raw JSON — this sells the "agent."
- Keep numbers honest and visible (including false positives) — credibility is part of the look.

## B6. Added stages (do AFTER Part-A Stage 9)

### Stage 10 — FastAPI wrapper (1 day)
- [ ] Wrap the engine: implement the B3 endpoints, returning render-ready JSON.
- [ ] Cache a run in memory so graph/rings/metrics all read the same result.
- [ ] Enable CORS for the React dev server.
- **Exit test:** hit each endpoint (curl / Swagger at /docs) → valid JSON for a real run.

### Stage 11 — React app: graph + dashboard (2–2.5 days) ← the visual payoff
- [ ] Vite + React + Tailwind scaffold; API client.
- [ ] Run bar + summary cards.
- [ ] Network graph with react-force-graph: coloring, sizing, hover, click, "rings only" filter.
- [ ] Ring list table wired to graph focus.
- **Exit test:** press Run → graph draws, rings are red, clicking a ring focuses it.

### Stage 12 — Case file + metrics views (1.5 days)
- [ ] Case-file slide-over with evidence subgraph + agent reasoning + action + confidence.
- [ ] Metrics view with the three charts (P/R, ₹ sweep, baseline delta) + benign results.
- **Exit test:** click a ring → case file opens with real agent output; metrics view renders.

### Stage 13 — Polish + demo + README (1 day)
- [ ] Loading/empty/error states; the graph-settling animation; dim-on-select.
- [ ] Record the walkthrough (§B7). Update README with screenshots + run steps for BOTH
      backend (`uvicorn`) and frontend (`npm run dev`).

## B7. Updated demo flow (screen recording)
1. Land on the dashboard, hit **Run detection** — watch the network graph form.
2. Rings glow red; summary cards fill in (#rings, ₹ at risk).
3. Click the biggest ring → graph focuses it → case-file panel slides in with the agent's
   evidence + reasoning + recommended action.
4. Toggle to show a **benign family** that shares an address — it's green, correctly cleared.
5. Open **Metrics** → graph-vs-baseline bar (we catch what per-transaction misses) + ₹ FP-cost.
This is far more compelling on screen than terminal output — lean into it.

## B8. Repo additions
```
abuse-ring-sentinel/
├── api/
│   └── main.py               # FastAPI app exposing the engine (B3 endpoints)
└── web/                      # React (Vite) app
    ├── src/
    │   ├── api/client.ts
    │   ├── components/NetworkGraph.tsx
    │   ├── components/RingList.tsx
    │   ├── components/CaseFilePanel.tsx
    │   ├── components/SummaryCards.tsx
    │   └── views/MetricsView.tsx
    └── ...
```

## B9. Scope discipline for the UI (cut in this order if short)
1. Cut the metrics *view* (keep metrics in eval/report.md) — but KEEP the network graph; it's
   the whole point of going visual.
2. Cut the "rings only" filter and animations.
3. Simplify the case file to a static panel (no slide-over).
Keep no matter what: the interactive network graph with red rings + clickable case file.
Never let the UI delay the engine — engine first, always.
