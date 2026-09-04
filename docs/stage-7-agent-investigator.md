# Stage 7 — Agent Investigator (in plain language)

This document explains what Stage 7 built, why it's a genuine tool-use loop rather than a
single prompt, how it decides ring-vs-benign, how the bounded-action policy keeps it from
being reckless, and the one real design deviation from PLAN.md (the LLM provider).

## 1. What problem is this trying to solve?

Stage 5's scorer gives every community a single number: "how ring-like does this look,
0 to 1." That's useful for ranking, but it's not an explanation, and it's definitely not
something you'd want to hand a fraud-ops analyst and say "trust this." A real investigation
needs to look at the *specific* evidence for *this* community, weigh it against the
specific ways an innocent group could look the same, and write down a reasoned verdict a
human can check. That's what Stage 7 adds on top of Stage 5's ranking: for each suspicious
community, an agent gathers evidence, reasons about it, and produces a case file.

## 2. Why a tool-use loop, and not just "paste the numbers into a prompt"

It would be much simpler to compute every feature and paste all of it into one big prompt
asking "ring or not?". We didn't do that, for the same reason a fraud analyst doesn't get
handed every fact about a case in one giant dump before they've even looked at who's
involved: the point of an *investigation* is that you decide what to look at next based on
what you already found. So `agent/investigator.py` runs a real loop — the agent calls a
tool, gets a result, decides whether it needs more evidence or is ready to conclude, and
only produces its final verdict once it has actually gathered what it needs. Concretely:
Claude/GPT calls `get_members` first, then chooses among `get_shared_entities`,
`get_events`, `get_account_ages`, and `compare_to_baseline` — it isn't forced through all
of them in a fixed order, but the system prompt requires it to have called at least
`get_shared_entities`, `get_events`, and `compare_to_baseline` before giving a verdict,
since a verdict with no behavioral evidence isn't defensible.

## 3. The five tools (`agent/tools.py`)

Every tool is a thin, deterministic wrapper over code Stages 3–5 already built — nothing
here re-derives numbers, it just packages them into a form the agent can ask for:

| Tool | What it returns | Reuses |
|---|---|---|
| `get_members` | Who's in the community + when each account was created | `data/customers.csv` |
| `get_shared_entities` | Which devices/cards/phones/addresses/ip_subnets/pincodes are shared by 2+ members, with edge weight and exactly who shares each one | `graph/build.py::internal_edges()` |
| `get_events` | Chargeback/return/COD-refusal counts, order volume, promo-code usage | `data/orders.csv`, `data/events.csv` |
| `get_account_ages` | Each member's account age + how tightly the group's signups are bunched | `data/customers.csv` |
| `compare_to_baseline` | The community's behavioral signals vs. the platform-wide baseline (event rate ratio, high-weight-edge share, timing burst, fresh-account ratio, promo concentration), with a plain-language interpretation guide for each number | `detect/features.py::compute_community_features()` |

The task spec says "keep the LLM out of arithmetic; it interprets tool results, it doesn't
compute densities" — every number above is already computed in Python before the agent
ever sees it. The agent's only job is to weigh what these numbers mean, not calculate them.

None of these tools ever return `ring_id` or `cluster_tag` (the ground-truth/debug labels
`graph/build.py` documents as off-limits to any detection logic) — the agent investigates
blind, the same way Stage 5's scorer does.

## 4. Ruling out benign explanations, on purpose, every time

The system prompt in `agent/investigator.py` explicitly names the four benign look-alikes
Stage 1's data generator injects — a family sharing an address/device, an office sharing an
IP subnet, a hostel/PG sharing a pincode, a couple sharing a card — and requires the agent
to check each one against the specific community before it's allowed to conclude "ring."
The dividing line it's told to use isn't "do they share something" (benign groups share
things too) but: do they *also* show an elevated bad-event rate, *and* a tight
account-creation burst, *and* is the sharing itself the strong kind (device/card, which
requires deliberate reuse) rather than only the weak kind (ip_subnet/pincode, which whole
neighborhoods share by accident)? A community that only clears the weak-sharing bar gets
cleared with `is_ring: false` and a reasoning string that names which benign explanation
fits — this is what the case files below show concretely.

## 5. The output contract

Every investigation ends in one strict JSON object:

```json
{
  "is_ring": true,
  "confidence": 0.93,
  "evidence": ["9 of 10 members share device ... (weight 1.0)", "event_rate_ratio 4.39x baseline", ...],
  "benign_explanations_considered": ["Family: ruled out because ...", "Office IP: ruled out because ...", ...],
  "reasoning": "...",
  "recommended_action": "block"
}
```

This is enforced with structured outputs (schema-validated JSON, not a hopeful prompt
instruction), so the happy path never needs ad-hoc parsing or a regex to extract fields.

## 6. Provider: OpenAI (gpt-4o), not Anthropic — a deliberate deviation from PLAN.md

PLAN.md's tech stack names "Claude (anthropic SDK)" for this stage. This build runs on the
**OpenAI Python SDK** (`openai`, Chat Completions API, `gpt-4o`) instead, because no
Anthropic API key was available in this environment and the project owner asked to use an
OpenAI key. Nothing else about the design changed — same manual tool-use loop, same five
tools, same strict-JSON verdict contract, same benign-explanation requirement, same
degraded-fallback failure handling. Only the wire format differs: tools are declared as
OpenAI "function" tools instead of Anthropic content blocks, tool results come back as
`role: "tool"` messages instead of `tool_result` blocks, and the strict JSON verdict is
enforced via `response_format: {"type": "json_schema", ...}` instead of Anthropic's
`output_config.format`. If an Anthropic key becomes available later, swapping the provider
back means rewriting `agent/investigator.py`'s request/response plumbing and
`agent/tools.py`'s `TOOL_DEFINITIONS` shape — the tool *implementations*
(`get_members`/`get_shared_entities`/etc.) and everything in `agent/policy.py` are
provider-agnostic and would not need to change.

## 7. Why the agent doesn't get the final word on action (`agent/policy.py`)

The agent proposes a `recommended_action` because it needs to reason about proportionality
to write a coherent case file, but an LLM's confidence number by itself is not "safe to
auto-block a real customer." `agent/policy.py` recomputes the actual bounded action from:

1. **Was evidence actually cited?** No evidence (or a degraded investigation) hard-caps the
   action at `manual_review`, no matter what the agent said — "no action without cited
   evidence" per the task spec.
2. **Does confidence clear a ₹-risk-tied bar for the action's severity?** `block` requires
   `confidence >= 0.85` (config.yaml's `block_confidence_threshold`); below that it steps
   down to `hold` (needs `>= 0.6`) or further to `manual_review`. The bar is set so that
   even under the agent's own stated uncertainty, the expected ₹ payoff of blocking stays
   positive — a wrong block costs `size × ₹1,500` (the `false_block_cost` in config.yaml)
   in lost legitimate customers, which is the same order of magnitude as the ₹ a real ring
   would otherwise cost in chargebacks, so a merely "pretty confident" verdict isn't enough
   to justify it.
3. **Was the verdict actually benign?** `is_ring: false` never justifies `hold`/`block`
   regardless of what the agent proposed.

The policy can only ever **downgrade** the agent's proposal, never upgrade it — an LLM
being cautious and recommending `monitor` is never second-guessed into something more
severe.

## 8. The case file

`agent/policy.py::write_case_file()` writes one JSON file per investigated community to
`agent/case_files/community_<id>.json` (gitignored — reproducible by re-running
`make investigate`, and not byte-identical across runs since it's LLM output). Each case
file contains the full tool-call trail (every tool call and its exact result, not just the
agent's summary of it), the verdict, and the policy's action plus its reasoning for any
downgrade — the point is that a human reviewer can check the agent's work against the raw
evidence without having to re-run anything.

## 9. Cost model (config.yaml)

Stage 5/6 left `cost_model` at `0` TODO placeholders. Stage 7 fills them in with rough
e-commerce SMB assumptions (not fitted to any real merchant):

- `avg_order_value`: ₹1,200
- `chargeback_cost`: ₹2,500 per bad event (fees + reverse logistics)
- `false_block_cost`: ₹1,500 per wrongly blocked legitimate customer
- `block_confidence_threshold`: 0.85, `hold_confidence_threshold`: 0.6

`agent/policy.py::load_cost_model()` fails loudly if any of these are missing or still `0`,
matching the fail-loudly convention `graph/weights.py` and `detect/community.py` already
established for `edge_weights` and `community_resolution`.

## 10. Failure handling

Per PLAN.md §10 ("LLM malformed JSON/timeout → one repair retry, else fall back to the
score-only verdict marked degraded"): `investigate_community` retries once on any exception
(API error, a tool-loop that somehow exhausts its iteration cap without a final answer,
etc.). If the retry also fails, it returns a `degraded` verdict — `is_ring: false`,
`confidence: 0.0`, evidence naming the raw Stage 5 features it fell back to, and the action
forced to `manual_review` — instead of crashing the whole investigation run. Verified with
a mocked client that always raises: the community still gets a usable, evidence-labeled
case file instead of an unhandled exception.

## 11. Verification performed without a live API key

At the time this stage was built, no LLM API key was available in the environment. Every
piece of deterministic logic was verified directly against the real dataset:

- All five tools in `agent/tools.py` were run against the actual graph/communities and
  produce correct, sane numbers — spot-checked against a known ring (community of 10,
  9/10 sharing one device, event_rate_ratio 4.39x baseline, timing_burst_score 0.946) and a
  known benign family (community of 7, only an address+pincode shared, event_rate_ratio
  1.07x baseline, timing_burst_score 0.0) — the same clean separation Stage 5's own exit
  test found.
- The full tool-use loop, the degraded-fallback path, and the policy/case-file pipeline
  were exercised end-to-end with a scripted fake OpenAI client standing in for the network
  call (fixed tool-call sequence, then a hand-written verdict matching what a real
  investigation of these two communities should plausibly conclude) — this validates every
  line of `investigator.py`'s message-passing and `policy.py`'s decision logic, but the
  verdict *content* in that dry run was scripted by the developer, not generated by an LLM.
  **The two case files this produced were deleted** rather than kept as if they were real
  agent output — they existed only to prove the wiring works. A real `make investigate` run
  (once `OPENAI_API_KEY` is set in `.env`) will produce genuinely LLM-authored case files
  for whichever ring and benign-family communities it selects.

## 12. Running it

```
make investigate
```

Requires `data/`, `data/resolved/`, and the graph to already exist (`make data resolve
graph`), and `OPENAI_API_KEY` set in `abuse-ring-sentinel/.env` (see `.env.example`). Picks
one pure ring community and one benign-family community (via ground truth, used only to
*select* an illustrative pair for the demo — never fed to the agent or its tools),
investigates both, prints the verdict + action, and writes both case files under
`agent/case_files/`.
