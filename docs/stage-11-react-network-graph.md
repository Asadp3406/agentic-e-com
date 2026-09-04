# Stage 11 — React App: Network Graph + Dashboard (in plain language)

This document explains what Stage 11 added: the first real screen of the web dashboard —
a React (Vite + TypeScript + Tailwind) app in `web/` that talks *only* to the FastAPI
backend from Stage 10, never to the Python engine directly. Pressing one button runs the
whole detection pipeline and draws the customer network as an interactive graph, with
fraud rings glowing red and everything else calm and green/grey.

No detection logic lives here, same rule as Stage 10 — this app only renders what
`GET`/`POST` calls to `api/main.py` return.

## 1. What problem is this trying to solve?

Up to Stage 10, "seeing" a result meant reading JSON in a terminal or opening Swagger UI.
That proves the API works, but it's not what a fraud-ops reviewer — or a hackathon judge —
actually wants to look at. PLAN.md's whole pitch for Part B is that a rendered evidence
subgraph is "the single most impressive thing in the demo." Stage 11 is where that
becomes real and clickable instead of a static PNG.

## 2. What was built

```
web/
├── index.html, vite.config.ts, tailwind.config.js, postcss.config.js, tsconfig.json
├── .env.example              # VITE_API_BASE_URL (defaults to http://localhost:8000)
└── src/
    ├── main.tsx, index.css   # app entry + Tailwind directives
    ├── App.tsx                # run bar + layout, wires everything together
    ├── api/client.ts          # typed fetch wrapper, one function per backend endpoint
    ├── components/
    │   ├── SummaryCards.tsx   # #rings, #accounts flagged, ₹ at risk, benign FP rate
    │   ├── NetworkGraph.tsx   # the hero screen — react-force-graph-2d
    │   └── RingList.tsx       # ranked table, wired to focus the graph
    │   ├── CaseFilePanel.tsx  # Stage 12 — left as a placeholder stub, not built yet
    └── views/
        └── MetricsView.tsx    # Stage 12 — left as a placeholder stub, not built yet
```

`CaseFilePanel.tsx` and `MetricsView.tsx` are explicitly PLAN.md §B6 Stage 12 scope (case
file slide-over + charts) — left untouched as one-line stub comments so Stage 12 has a
clean starting point, per the project's stage-by-stage build convention.

### `src/api/client.ts` — the only place that knows about HTTP

One TypeScript function per backend endpoint (`runDetection`, `getGraph`, `getRings`,
`getRing`, `getMetrics`, `getBenign`), each with a hand-written interface that matches
`api/main.py`'s actual JSON shape field-for-field (read directly from the Stage 10 source,
not guessed). A small `request()` helper adds one behavior on top of plain `fetch`: a
non-2xx response throws an `ApiError` carrying the FastAPI `detail` message, so a 409
("no run yet") reads as a real, catchable error instead of a generic "fetch failed."

### The run bar + `SummaryCards` (`App.tsx`, `SummaryCards.tsx`)

Pressing **Run detection**:
1. `POST /api/run` (button shows "Running detection…"),
2. then `GET /api/graph` and `GET /api/rings` in parallel (button shows "Loading
   results…"),
3. then the graph and ring list render and the button flips to "Re-run detection."

Any failure at any step (most likely: API not running on :8000) shows a red banner with
the actual error message rather than a silent blank screen.

Summary cards are computed client-side from the `GET /api/rings` response — no new
backend endpoint needed, since `/api/rings` already carries `status` (suspicious /
cleared / unlabeled) and `ground_truth` per community:
- **Rings found** = count of communities with `status: "suspicious"`.
- **Accounts flagged** = sum of `.size` over those.
- **₹ at risk** = sum of `.rupee_risk` over those (same number the backend already
  computes as `size × chargeback_cost`, per Stage 10's convention).
- **False-positive rate on benign set** = (benign-ground-truth communities marked
  suspicious) / (all benign-ground-truth communities) — a quick approximation from
  `/api/rings` alone; `GET /api/benign` (Stage 10, already live) has the precise,
  per-cluster version of this and is what Stage 12's metrics view should switch to.

### `NetworkGraph.tsx` — the hero screen

Uses `react-force-graph-2d` (the exact library PLAN.md names) fed directly by `GET
/api/graph`'s `{nodes, edges}` payload — no reshaping, per Stage 10's "render-ready JSON"
contract.

- **Color**: customer nodes in a ring (`suspicious: true`) are red; everyone else is
  green; shared-entity nodes (device/card/phone/address/ip_subnet/pincode) are neutral
  grey.
- **Size**: node radius scales with the backend's `risk` score (0–1), so the riskiest
  accounts visually pop even before you click anything.
- **Hover tooltip**: shows node id, type, ring/benign status, risk score, cluster number,
  and (for customer nodes) the ground-truth `ring_id`/`cluster_tag` the backend already
  includes — useful for building trust in a demo, not fed back into any scoring.
- **Click → focus a cluster**: clicking a customer node (or a `RingList` row — both drive
  the same `selectedClusterId` state in `App.tsx`) dims every node/edge outside that
  community to near-invisible and zooms the camera to fit just that cluster plus the
  shared-entity nodes it's directly connected to (so the device/card/etc. tying the ring
  together stays visible, not just the customer dots). Clicking empty background clears
  the selection.
- **"Rings only" filter**: a checkbox in the top bar that restricts the rendered graph to
  customers belonging to a community that contains at least one flagged member, plus
  their shared entities — computed client-side from the same `/api/graph` payload, no
  extra request.
- **Legend**: bottom-left overlay explaining the three node colors and what size/line
  thickness encode.
- **Settling animation**: `cooldownTicks={100}` lets `react-force-graph`'s underlying
  d3-force simulation run and settle naturally (nodes drift into their force-directed
  positions rather than snapping there), then `zoomToFit` is called once after the sim
  has had time to relax — this is the "network forming" effect PLAN.md §B5 asks for.

### `RingList.tsx`

A ranked table (highest score first) of every community `GET /api/rings` returns: id,
size, score, ₹ risk, and a color-coded status chip (red "suspicious" / green "cleared" /
grey "unlabeled"). Each row's title attribute shows the ground-truth label on hover (e.g.
`RING015 (5/5)` or `benign:family (7/7)`) for the same "own your false positives"
transparency the eval docs already practice. Clicking a row sets `selectedClusterId` in
`App.tsx`, which `NetworkGraph` picks up and focuses — the same mechanism a graph-node
click uses, so the two interactions stay in sync (selecting via the table highlights the
matching row too).

## 3. Design choices (PLAN.md §B5)

- **Dark theme**: `neutral-950`/`neutral-900` background, high-contrast white/neutral text,
  generous padding on cards and table cells.
- **Two accent colors only**: red (`danger`, `#ef4444`/`#f87171`) for anything
  ring/suspicious, green (`safe`, `#22c55e`/`#4ade80`) for anything benign/cleared —
  defined once in `tailwind.config.js` and reused everywhere (cards, status chips, graph
  nodes) rather than picked ad hoc per component.
- **Generous spacing**: a 6-unit page gutter, 4-unit gaps between panels, cards with
  visible padding rather than a dense terminal-like layout.

## 4. `make web`

```
web-install:
	cd web && npm install

web:
	cd web && npm run dev
```

`make web-install` once, then `make web` starts the Vite dev server on port 5173 (matches
the CORS origin Stage 10's `api/main.py` already allows). `VITE_API_BASE_URL` in
`web/.env` overrides the API origin if the backend isn't on `localhost:8000`.

## 5. Verification performed

Ran both servers for real — `.venv/bin/uvicorn api.main:app --port 8000` and `npm run dev`
in `web/` — and drove the actual browser (headless Chromium via Playwright, used only as a
verification tool for this session, not added to the project) against `localhost:5173`:

1. Loaded the page cold: empty state, "Press 'Run detection' to draw the network."
2. Clicked **Run detection**: button showed "Running detection…" then "Loading
   results…", then real numbers rendered — **21 rings found, 111 accounts flagged, ₹2,77,500
   at risk, 0.0% false-positive rate on the benign set** — and the network graph drew and
   settled with visibly red ring-member nodes scattered through a mostly grey/green
   network of ~4,100 nodes.
3. Clicked the top row of the ranked table (community #11, size 5, score 0.911): the
   graph dimmed everything else and zoomed in on exactly that community — 5 red customer
   nodes radiating from one shared grey entity node in the center, matching the "device
   shared by 5/5 members" story Stage 3/5's own docs tell about this kind of ring.
4. Checked the browser console during the whole flow: zero errors.

This is the PLAN.md §B6 Stage 11 exit test, met exactly: *"press Run → graph draws, rings
are red, clicking a ring focuses it."*

## 6. What Stage 12 (case file + metrics views) needs to know

- `CaseFilePanel.tsx` and `MetricsView.tsx` are still untouched one-line stubs — not
  wired into `App.tsx` at all yet. `GET /api/rings/{id}` (case file) and `GET
  /api/metrics` / `GET /api/benign` (metrics) are already live and fully typed in
  `src/api/client.ts` (`getRing`, `getMetrics`, `getBenign`) — Stage 12 should call those
  directly rather than adding new client functions.
- `App.tsx` already tracks `selectedClusterId` as shared state between the graph and the
  ring list. The natural hook point for a case-file slide-over is the same state: when it
  becomes non-null (from either a graph click or a table-row click), Stage 12 can also
  trigger `getRing(selectedClusterId)` and render the panel — no new state plumbing
  needed, just a new consumer of the existing selection.
- `GET /api/rings/{id}` is slow the first time a given community is opened (a real LLM
  call) and instant after — Stage 10's doc already flags this; Stage 12's case-file panel
  needs its own loading state for that fetch specifically, separate from the initial
  "Run detection" loading state.
- `SummaryCards`' false-positive-rate number is a client-side approximation from
  `/api/rings`; Stage 12's metrics view should switch to `GET /api/benign`'s precise
  `n_false_positives / n_benign_clusters`, and `App.tsx`'s summary calculation is a
  reasonable thing to simplify/remove at that point rather than maintaining two versions
  of the same number.
- No test framework or component tests were added — verification for this stage was a
  real, driven browser session (see §5) rather than unit tests, consistent with this
  being a visual/interactive deliverable.
