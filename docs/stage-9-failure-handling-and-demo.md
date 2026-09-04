# Stage 9 — Failure Handling + Engine Demo (in plain language)

This document explains what Stage 9 added: a review of every failure-handling requirement
PLAN.md §10 asks for, a way to force the one failure mode that can't be triggered just by
running the pipeline normally (an LLM error), and `demo/run_demo.py` — a single command
that runs the whole engine, shows both a real ring investigation and a real benign
clearance, demonstrates the forced failure recovering gracefully, and saves a picture of
the evidence a ring leaves behind. This is deliberately the *engine-level* demo — no web
UI yet (that's Part B, Stages 10-13) — so there is already something concrete to point at
before any frontend work starts.

## 1. What problem is this trying to solve?

Every earlier stage built one piece of the pipeline and proved it works on good data. A
real system also has to survive *bad* data and things going wrong mid-run without
crashing the whole batch or silently producing garbage. PLAN.md §10 lists four specific
failure modes a judge will look for. Three of them turned out to already be handled by
earlier stages as a natural consequence of how they were built — Stage 9's job for those
three was to confirm it, not build it fresh. The fourth (an LLM failure) needed something
new: a way to actually *show* it happening, since a real OpenAI outage isn't something you
can schedule for a demo.

## 2. The four failure modes, one by one

### 2.1 Bad/missing attribute rows → entity resolution isolates them

**Already handled, in Stage 2.** `resolve/entity_resolution.py`'s `resolve_phones()` and
`resolve_ips()` don't crash or drop a row when a phone number is unparseable or an IP
address is malformed — they give that row its own private "entity of one"
(`UNRESOLVED_<row_id>`) so it simply never merges with anyone else's identity, and the
rest of the pipeline keeps running exactly as if that row's attribute were just weak
evidence rather than missing. Nothing changed here for Stage 9 — this section documents
*that* it was already true and points at the exact lines that make it true
(`resolve/entity_resolution.py`'s `resolve_phones`/`resolve_ips`, both flagged in their own
docstrings as this failure-handling rule).

### 2.2 LLM malformed JSON / timeout → one repair retry, else a `degraded` verdict

**Already handled in Stage 7, now demonstrable on demand.** `agent/investigator.py`'s
`investigate_community()` already wrapped the whole tool-use loop in a retry-once-then-
degrade pattern: if the model call raises for any reason (a timeout, a 5xx, output the
structured-outputs contract couldn't validate), it waits a second and tries the entire
investigation again from scratch; if the second attempt also fails, it returns an
`InvestigationResult` with `degraded=True`, `confidence=0.0`, `is_ring=False`, and evidence
that names the raw Stage 5 features it fell back to instead of an LLM opinion — no
exception escapes to crash the batch.

The gap Stage 9 closed: there was no way to *make* this happen without either physically
breaking the network or getting lucky with a real API hiccup, which is not something you
want to depend on for a demo. So `agent/investigator.py`'s tool-use loop now checks one
environment variable, `FORCE_LLM_FAILURE=1`, at the top of every iteration, and raises
immediately if it's set — *before* any real request goes to OpenAI. Setting this env var
makes the retry-then-degrade path run for real: two simulated failures, a real one-second
sleep between them (the actual retry code path, not a shortcut around it), then a real
`degraded` case file. Unset it and everything behaves exactly as before — this is purely a
demo/test hook, not a behavior change to the normal path.

### 2.3 Agent over-reach guard → no `block` without evidence above the cost-tied confidence

**Already handled in Stage 7.** `agent/policy.py::decide_action()` is a pure downgrade
cascade: it can only ever make the agent's proposed action *less* drastic, never more.
`block` requires both a non-empty evidence list *and* confidence at or above
`block_confidence_threshold` (0.85); a `degraded` verdict has no real evidence by
construction and is hard-capped at `manual_review` regardless of what it proposes.
Stage 9 didn't change any of this — `demo/run_demo.py` just prints the policy's decision
next to the agent's raw recommendation for every investigation, so a downgrade (if one
happens) is visible in the terminal output, not something you'd have to go dig through a
case file to notice.

### 2.4 Giant community → cap + flag, don't hang

**Already handled in Stage 4.** `detect/community.py::flag_giant_communities()` checks
every returned community against a size threshold (20% of all clustered customers) and
returns a flag — it never raises, never drops the community, never blocks the pipeline
from continuing. `demo/run_demo.py` prints these flags (if any exist) right after
detecting communities, the same way `detect/community.py`'s own `main()` already does. On
this project's tuned dataset/config there are zero giant-blob flags (documented back in
Stage 4), so the demo's own run won't show one — the flag-printing code path exists and
was exercised directly against synthetic oversized input during Stage 4 itself, per that
stage's log entry.

## 3. `demo/run_demo.py` — what it actually does, step by step

The whole point of this file is: **one command, and you can see the whole story** — not
just numbers, but an actual ring being investigated, an actual benign group being cleared,
a failure being survived, and a picture of what the evidence looks like. It calls every
earlier stage's real code — nothing here recomputes a score or reimplements a check, it's
pure orchestration and printing, same convention as every prior stage.

1. **Build the graph + detect communities** (Stages 3-4) — prints node/edge counts,
   how many communities were found, and any giant-blob flags.
2. **Score every community** (Stage 5) — prints the top 10 by score, each with its
   dominant feature and a ground-truth label *for reporting only* (never used to pick
   what gets investigated). The demo picks its "suspect" as whichever community the
   scorer itself ranked #1 — not a ground-truth lookup like some earlier stages' exit
   tests used to pick an illustrative example. This matters: it proves the pipeline finds
   its own top suspect the same way it would on real, unlabeled data. It picks its
   "benign look-alike" as the highest-scoring community that ground truth says is *not*
   a ring, specifically to show the near-miss case (the benign cluster that scored
   closest to ring-like) getting correctly talked down rather than an easy, obviously-
   clear case.
3. **Investigate both with the real agent + policy** (Stage 7) — a real OpenAI call for
   each, a real cost-aware policy decision, a real case file written to
   `agent/case_files/`.
4. **Force one LLM failure** (§2.2 above) on the same suspect community and show the
   `degraded` verdict come back as a case file instead of an exception.
5. **Render the evidence subgraph** — reuses `graph/visualize.py`'s exact drawing code
   (same colors, same legend, same layout logic as Stage 3's ad-hoc sanity check) so the
   demo's picture uses the same visual language as every other figure in this project,
   rather than inventing a new one. Saves to `demo/evidence_subgraph.png`: the top-ranked
   ring on the left, the top-ranked benign look-alike on the right, side by side.

## 4. What the real runs actually showed (including an honest miss)

Run twice against this repo's real synthetic dataset with a real `OPENAI_API_KEY` (the
demo was re-run after a bug fix, described in §5's changelog — both runs are reported
here rather than only the flattering one, per this project's own honesty-first
convention from Stage 8):

- **Top-ranked community (#11, 5 members, score 0.912)** was, per ground truth, RING015 —
  the scorer's own #1 pick was a real ring, no cherry-picking needed. The agent
  investigated it independently (never told the ground-truth label) and correctly called
  it a ring **in both runs**, at confidence 0.95, citing: all 5 members sharing one card
  (`CARD_ENT000115`, weight 0.9), an event rate 8.67x the global baseline, a timing-burst
  score of 0.99 (near-simultaneous account creation), and a fresh-account ratio of 1.0.
  Policy's action: **block** — confidence clears the 0.85 threshold, evidence is cited, no
  downgrade needed.
- **Top-ranked benign look-alike (#71, 3 members, score 0.582, ground truth
  `cluster_tag=independent`)** — the closest a genuinely benign community got to looking
  like a ring, and specifically the harder of the two cases in this demo. The two runs
  disagreed:
  - **Run 1: correct.** `is_ring: false` at confidence 0.60, citing only mild event-rate
    elevation (1.76x baseline, far below the ring's 8.67x) and no creation burst
    (timing-burst score 0.443). Policy action: `manual_review`.
  - **Run 2: wrong — a false positive.** The same community, same tools, same evidence
    available, but this time the model weighted the one shared card (weight 0.9, but
    only between 2 of the 3 members, not all of them the way the real ring's card was
    shared by all 5) heavily enough to call `is_ring: true` at confidence 0.85, citing
    "elevated event rate" (1.76x — the same number Run 1 correctly called "mild") and a
    partial fresh-account ratio (0.667) as if it were a burst. Policy's `block_confidence_
    threshold` (0.85) was just barely cleared, so the policy layer did **not** catch this
    one — it correctly enforced its own rule (confidence >= 0.85 + evidence cited = block
    allowed), but the rule can't protect against the agent being overconfident in the
    first place.
  - **This is reported here, not hidden, on purpose.** It's the honest answer to "is the
    agent reliable," not just "did it work once." The two runs used an identical prompt,
    identical tool results, identical cost model — the only variable was the LLM's own
    non-determinism on a genuinely ambiguous case (3 people, a partial card share, mildly
    elevated events — this is much closer to PLAN.md §8's deliberately-ambiguous "shares
    a device AND has slightly elevated returns" trap than to an easy call). A single
    5-member ring with strong, unanimous evidence was correctly identified twice in a row;
    a 3-member near-miss with partial, weaker evidence was correctly identified once and
    incorrectly escalated once. The practical takeaway for anyone building on this: don't
    trust a single LLM call on a borderline case for anything as consequential as `block`
    — an ensemble/self-consistency check (e.g. investigate ambiguous-scoring communities
    twice and require agreement before allowing `block`) would be a reasonable next
    hardening step, not implemented here since it's outside Stage 9's scope, but worth
    flagging for whoever picks this up next.
- **Forced failure on community #11** — `FORCE_LLM_FAILURE=1` triggered two consecutive
  simulated errors (the real retry-once code path ran, including its one-second wait),
  then a `degraded=True` verdict with `manual_review` forced regardless of how confident
  the *previous real run* had been. The pipeline did not crash and produced a normal case
  file, saved to `agent/case_files/demo_forced_failure/community_11.json` (a separate
  subdirectory, see §5 below for why) — one that honestly says "the LLM investigation
  failed, here are the raw features instead, a human needs to look at this."
- **Evidence subgraph** (`demo/evidence_subgraph.png`) — visibly denser, thicker,
  redder edges on the ring side (device/card sharing among all 5 members) versus a
  sparser web of thin blue/purple/grey edges on the benign side (address/phone/pincode,
  plus one partial card share between 2 of 3 members), the same ring-vs-benign contrast
  Stage 3 first demonstrated, now automatically generated from the pipeline's own top
  picks rather than a hand-picked example.

## 5. A case-file-overwrite bug found while building this

The real investigation (step 3) and the forced-failure demo (step 4) deliberately target
the *same* top-ranked community, so the before/after contrast is easy to follow. The first
version of `demo/run_demo.py` wrote both through `agent/policy.py::write_case_file()`'s
default `CASE_FILE_DIR`, which names files only by community id (`community_11.json`) —
so the forced-failure run was silently overwriting the real verdict's case file on disk.
Fixed by writing the forced-failure demo's case file to a separate
`agent/case_files/demo_forced_failure/` subdirectory instead, so both the real verdict and
the degraded one survive one `make demo` run and can be diffed side by side.

## 6. Why this doesn't re-run the eval

`eval/run_eval.py` (Stage 8) already answers "how good is this, precisely" with real
precision/recall/₹ numbers across the *entire* dataset. `demo/run_demo.py` answers a
different question — "show me one concrete example of it working, live" — which is a
demo's job, not an eval's. Running the full eval every time you want to show the story
would be slow and would bury the one ring/one benign example a viewer actually wants to
see under a wall of aggregate statistics. They're complementary, not overlapping: `make
eval` for the honest numbers, `make demo` for the readable walkthrough.

## 7. How to run it

```
make demo
```

Requires `OPENAI_API_KEY` set in `abuse-ring-sentinel/.env` (falls back to a printed
warning and the real investigations will fail, but the forced-failure demo and the
evidence-subgraph render still work without a key). Requires `make data resolve graph` to
have been run at least once so `data/*.csv` and `data/resolved/*.csv` exist.

To force the failure path standalone, outside the full demo:

```
FORCE_LLM_FAILURE=1 .venv/bin/python -c "
from agent.investigator import investigate_community
from detect.community import detect_communities
from graph.build import build_graph
g = build_graph().graph
c = detect_communities(g).communities
print(investigate_community(g, c, 0))
"
```
