"""Stage 7: LLM tool-use loop that investigates one community at a time and emits a
structured ring-vs-benign verdict.

PROVIDER: OPENAI (gpt-4o), NOT ANTHROPIC
--------------------------------------------
PLAN.md's tech stack names "Claude (anthropic SDK)" for this stage, but this project runs
on the OpenAI SDK instead (`openai` Python package, Chat Completions API) -- a deliberate
choice made when no Anthropic API key was available. Everything else about the design
(manual tool-use loop, tool schemas in agent/tools.py, strict JSON verdict schema, the
ruling-out-benign-explanations requirement, the degraded-fallback failure handling) follows
the same spec regardless of provider; only the wire format changes:
  - tools are declared as OpenAI "function" tools (agent/tools.py::TOOL_DEFINITIONS)
  - the model requests calls via `message.tool_calls`, not Anthropic `tool_use` blocks
  - tool results are returned as `{"role": "tool", "tool_call_id": ..., "content": ...}`
    messages, not a single `tool_result` content block list
  - the final structured verdict is enforced via Chat Completions'
    `response_format={"type": "json_schema", "json_schema": {..., "strict": True}}`,
    OpenAI's equivalent of Anthropic's `output_config.format`

WHY A MANUAL LOOP, NOT THE ASSISTANTS/RESPONSES API
-------------------------------------------------------
OpenAI offers higher-level agent surfaces (Assistants API, Responses API with built-in
tool-loop helpers), but for an auditable fraud-decision case file we want the loop explicit
and inspectable -- every tool call and result is captured into the case file's evidence
trail as it happens, not hidden inside a managed run object. This is the standard Chat
Completions manual loop: call the model, if it wants a tool run it deterministically
(agent/tools.py), feed the result back as a `tool` message, repeat until the model returns
a plain content message instead of tool calls.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
--------------------------------------------
It never computes a density, a ratio, or a rate -- every number the agent reasons over was
already computed by agent/tools.py (which in turn reuses detect/features.py, the exact same
code Stage 5's scorer trusts). The LLM's only job is interpretation: given these tool
results, is this coordinated fraud or an innocent look-alike, and how confident is that
call. This keeps arithmetic auditable/deterministic and keeps the LLM's job to what LLMs are
actually good at -- weighing qualitative evidence and writing a defensible explanation.

RULING OUT BENIGN EXPLANATIONS IS NOT OPTIONAL
-------------------------------------------------
The system prompt below explicitly requires the agent to name and address the standard
benign look-alikes this project's own data generator injects (family, office IP cluster,
hostel/PG pincode cluster, a couple sharing a card) before it's allowed to conclude "ring" --
mirroring PLAN.md's Stage 7 exit test ("on a benign family -> agent explicitly clears it").
A community that shares a pincode/ip_subnet but has baseline event rates and no timing burst
should come back `is_ring: false` with reasoning that names which benign explanation fits.

OUTPUT CONTRACT
-----------------
Strict JSON per community, matching the task spec:
  {is_ring, confidence, evidence: [...], reasoning, recommended_action}
`evidence` is a list of short, cited strings referencing actual tool results (e.g. "5 of 7
members share device device:DEV00123 (weight 1.0)") -- not freeform claims. Enforced via
OpenAI structured outputs (`response_format` json_schema, `strict: True`), which guarantees
the final response is schema-valid JSON, no ad-hoc parsing/repair needed for the happy path.

FAILURE HANDLING (PLAN.md SS10)
-----------------------------------
If the model produces something structured-outputs can't validate, or the API call itself
fails (timeout, 5xx), one retry is attempted; if that also fails, `investigate_community`
returns a `degraded` verdict (is_ring unknown, confidence 0.0, action forced to
`manual_review`) rather than crashing the batch -- one bad community should not lose the
whole run's case files.

FORCING A FAILURE ON DEMAND (FOR THE DEMO)
---------------------------------------------
Setting the env var `FORCE_LLM_FAILURE=1` makes every call in `_run_tool_loop` raise a
`RuntimeError` immediately, before any real API request goes out. This exists purely so
`demo/run_demo.py` (and anyone else) can *show* the degraded path on demand -- retry, then
a `degraded` case file with a forced `manual_review` action -- without needing to actually
break a network connection or bribe the model into returning malformed JSON. It is checked
inside the retry loop (not before it), so the forced run still exercises the real one-retry
behavior, not a shortcut around it.

recommended_action FROM THE AGENT IS ADVISORY ONLY
------------------------------------------------------
The agent proposes an action as part of its reasoning (it needs to reason about
proportionality to write a coherent case file), but agent/policy.py has the final word --
it recomputes the bounded action from the cost model and the agent's confidence/evidence,
and can downgrade (never upgrade) what the agent proposed. See policy.py's module docstring.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from agent.tools import TOOL_DEFINITIONS, ToolContext, execute_tool

REPO_ROOT = Path(__file__).resolve().parents[1]

MODEL = "gpt-4o"
MAX_TOOL_ITERATIONS = 8  # hard cap so a confused loop can't spin forever (failure handling)

VERDICT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "is_ring": {"type": "boolean"},
        "confidence": {
            "type": "number",
            "description": "0.0-1.0 confidence in the is_ring verdict.",
        },
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short, specific, cited facts from tool results that support the verdict (e.g. exact entity ids, ratios, member counts). No vague claims.",
        },
        "benign_explanations_considered": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Which benign look-alike explanations (family, office IP, hostel/PG pincode, couple sharing a card, coincidental overlap) were checked, and why each was ruled in or out for this specific community.",
        },
        "reasoning": {
            "type": "string",
            "description": "A short paragraph explaining the verdict, referencing the evidence and the benign explanations considered.",
        },
        "recommended_action": {
            "type": "string",
            "enum": ["monitor", "hold", "manual_review", "block"],
            "description": "The agent's proposed bounded action. This is advisory -- agent/policy.py makes the final, cost-aware call and may downgrade it.",
        },
    },
    "required": [
        "is_ring",
        "confidence",
        "evidence",
        "benign_explanations_considered",
        "reasoning",
        "recommended_action",
    ],
    "additionalProperties": False,
}

VERDICT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "ring_verdict",
        "schema": VERDICT_JSON_SCHEMA,
        "strict": True,
    },
}

SYSTEM_PROMPT = """You are a fraud-ring investigator for an e-commerce platform. You are given \
one suspicious cluster of customer accounts (a "community") that a graph-based detector has \
already flagged as worth investigating. Your job is to determine whether this is a coordinated \
fraud RING or a BENIGN CO-LOCATION (people who are innocently connected but not committing fraud).

You have tools to pull evidence about this community: its members, what identity attributes \
they share (device/card/phone/address/ip_subnet/pincode), their chargeback/return/COD-refusal \
history, their account-creation timing, and how their behavioral signals compare to the \
platform-wide baseline. Use the tools to gather evidence before concluding anything -- do not \
guess. Call get_members first, then whichever other tools you need; you do not have to call \
every tool if the evidence is already clear, but you must call at least get_shared_entities, \
get_events, and compare_to_baseline before giving your final verdict, since a verdict without \
behavioral evidence is not defensible.

You MUST actively try to RULE OUT benign explanations before concluding "ring". Real e-commerce \
platforms have lots of innocently connected accounts that a naive detector would false-positive \
on:
  - a FAMILY sharing one address, and sometimes one device (a shared household tablet/laptop)
  - an OFFICE or co-working space where many employees share one IP subnet
  - a HOSTEL or PG (paying-guest) building where many unrelated tenants share one pincode
  - a COUPLE sharing one card or one device

The signal that separates a real ring from these is not "do they share something" -- benign \
groups share things too. It is: do they ALSO show elevated chargeback/return/COD-refusal rates \
well above baseline, AND a tight account-creation burst, AND is the sharing itself the strong \
kind (device/card, which requires deliberate reuse) rather than only the weak kind (ip_subnet/ \
pincode, which whole neighborhoods share by accident)? A cluster that shares only a pincode or \
ip_subnet, with baseline-normal event rates and accounts created over a long span, is the \
textbook benign case and should be cleared with is_ring: false, explicitly naming which benign \
explanation fits. A cluster with strong shared entities (device/card) AND an elevated event rate \
AND a tight creation burst is the textbook ring.

Some clusters are genuinely ambiguous (e.g. shared device with slightly elevated returns but no \
chargebacks and no timing burst) -- for those, it is correct to report lower confidence and \
recommend a cautious action (hold or manual_review) rather than forcing a confident verdict \
either way.

Do not perform arithmetic yourself or restate raw numbers as if you computed them -- the tool \
results already contain the exact ratios/rates/counts you need; cite them directly in your \
evidence list. Every piece of evidence you cite must trace back to an actual tool result from \
this investigation, and every conclusion must be supported by at least one cited piece of \
evidence -- do not recommend `block` or state high confidence without citing the specific \
evidence that justifies it.

When you are ready to give your final verdict (after calling the required tools), respond with \
the verdict JSON directly instead of calling another tool."""


@dataclass
class ToolCallRecord:
    """One tool call + its result, kept for the case file's audit trail."""

    tool_name: str
    tool_input: dict
    result: dict


@dataclass
class InvestigationResult:
    community_id: int
    size: int
    members: list[str]
    is_ring: bool
    confidence: float
    evidence: list[str]
    benign_explanations_considered: list[str]
    reasoning: str
    recommended_action: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None


def _client():
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(REPO_ROOT / ".env")  # picks up OPENAI_API_KEY
    return OpenAI()


def _run_tool_loop(
    client,
    ctx: ToolContext,
    community_id: int,
    communities: list[list[str]],
    size: int,
) -> tuple[dict, list[ToolCallRecord]]:
    """The manual agentic loop: call the model, execute any requested tools, feed results
    back as `tool` messages, repeat until the model responds with plain content instead of
    tool calls. The final content is validated against VERDICT_JSON_SCHEMA by
    `response_format` (structured outputs, strict mode)."""
    user_prompt = (
        f"Investigate community #{community_id} ({size} members). Gather evidence with "
        "the available tools, then give your final verdict as the required JSON structure."
    )
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    tool_calls: list[ToolCallRecord] = []

    for _ in range(MAX_TOOL_ITERATIONS):
        if os.environ.get("FORCE_LLM_FAILURE") == "1":
            raise RuntimeError(
                "forced failure (FORCE_LLM_FAILURE=1) -- simulating an LLM timeout/error "
                "for the Stage 9 degraded-path demo"
            )
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            response_format=VERDICT_RESPONSE_FORMAT,
        )
        choice = response.choices[0]
        message = choice.message

        requested_calls = message.tool_calls or []

        if not requested_calls:
            if message.content is None:
                raise RuntimeError(f"model stopped without a tool call or final answer (finish_reason={choice.finish_reason!r})")
            return json.loads(message.content), tool_calls

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in requested_calls
                ],
            }
        )

        for tc in requested_calls:
            tool_input = json.loads(tc.function.arguments) if tc.function.arguments else {}
            result = execute_tool(ctx, community_id, communities, tc.function.name, tool_input)
            tool_calls.append(ToolCallRecord(tool_name=tc.function.name, tool_input=tool_input, result=result))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                }
            )

    raise RuntimeError(f"exceeded MAX_TOOL_ITERATIONS ({MAX_TOOL_ITERATIONS}) without a final verdict")


def investigate_community(
    graph: nx.Graph,
    communities: list[list[str]],
    community_id: int,
    client=None,
    data_dir: Path = None,
) -> InvestigationResult:
    """Investigate one community. On any failure (malformed output the SDK couldn't
    validate, API error, exhausted tool-call budget), retries once; if that also fails,
    returns a `degraded` verdict rather than raising, per PLAN.md SS10's failure-handling
    requirement ("LLM malformed JSON/timeout -> one repair retry, else fall back to a
    score-only verdict marked degraded")."""
    from detect.features import compute_community_features

    members = communities[community_id]
    size = len(members)
    ctx = ToolContext.build(graph, data_dir=data_dir) if data_dir else ToolContext.build(graph)
    client = client or _client()

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            verdict, tool_calls = _run_tool_loop(client, ctx, community_id, communities, size)
            return InvestigationResult(
                community_id=community_id,
                size=size,
                members=members,
                is_ring=bool(verdict["is_ring"]),
                confidence=float(verdict["confidence"]),
                evidence=list(verdict["evidence"]),
                benign_explanations_considered=list(verdict["benign_explanations_considered"]),
                reasoning=str(verdict["reasoning"]),
                recommended_action=str(verdict["recommended_action"]),
                tool_calls=tool_calls,
            )
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
            last_error = exc
            if attempt == 0:
                time.sleep(1.0)
            continue

    # Degraded fallback: score-only verdict, no LLM interpretation available. Falls back
    # to Stage 5's own features so the pipeline still produces something usable, forced to
    # the most conservative action since no evidence-backed reasoning exists.
    features = next(
        f for f in compute_community_features(graph, communities, data_dir=data_dir or (REPO_ROOT / "data")) if f.community_index == community_id
    )
    return InvestigationResult(
        community_id=community_id,
        size=size,
        members=members,
        is_ring=False,
        confidence=0.0,
        evidence=[
            f"DEGRADED: agent investigation failed ({last_error!r}); falling back to raw "
            f"Stage 5 features only: event_rate_ratio={features.event_rate_ratio:.2f}, "
            f"high_weight_edge_share={features.high_weight_edge_share:.2f}, "
            f"timing_burst_score={features.timing_burst_score:.2f}"
        ],
        benign_explanations_considered=[],
        reasoning=(
            "The LLM investigation could not be completed (see evidence). No ring-vs-benign "
            "reasoning is available; this community requires manual review before any action "
            "beyond monitoring is taken."
        ),
        recommended_action="manual_review",
        tool_calls=[],
        degraded=True,
        degraded_reason=repr(last_error),
    )


def investigate_communities(
    graph: nx.Graph,
    communities: list[list[str]],
    community_ids: list[int],
    client=None,
    data_dir: Path = None,
) -> list[InvestigationResult]:
    """Investigate several communities in sequence (one conversation per community --
    community-level context should never leak between investigations, so no shared history)."""
    client = client or _client()
    return [
        investigate_community(graph, communities, cid, client=client, data_dir=data_dir)
        for cid in community_ids
    ]


def main() -> None:
    """Exit test: pick one real ring and one benign look-alike (via ground truth, used here
    ONLY to select illustrative examples for the demo -- never fed to the agent or the
    tools, see agent/tools.py's module docstring), investigate both, print verdicts."""
    import pandas as pd

    from detect.community import detect_communities
    from graph.build import build_graph

    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "Warning: OPENAI_API_KEY is not set in the environment or .env. This will "
            "fail unless the OpenAI client can resolve credentials another way."
        )

    build_result = build_graph()
    graph = build_result.graph
    community_result = detect_communities(graph)
    communities = community_result.communities

    customers = pd.read_csv(REPO_ROOT / "data" / "customers.csv", dtype=str)
    ring_by_customer = dict(zip(customers["customer_id"], customers["ring_id"].fillna("")))
    tag_by_customer = dict(zip(customers["customer_id"], customers["cluster_tag"].fillna("")))

    def ring_purity(members: list[str]) -> tuple[str, float]:
        ring_ids = [ring_by_customer.get(m, "") for m in members if ring_by_customer.get(m, "")]
        if not ring_ids:
            return "", 0.0
        from collections import Counter

        counts = Counter(ring_ids)
        top_ring, top_count = counts.most_common(1)[0]
        return top_ring, top_count / len(members)

    def benign_tag(members: list[str]) -> str:
        tags = [tag_by_customer.get(m, "") for m in members]
        counts: dict[str, int] = {}
        for t in tags:
            if t:
                base = t.rsplit("_", 1)[0] if t[-1].isdigit() else t
                counts[base] = counts.get(base, 0) + 1
        return max(counts, key=counts.get) if counts else ""

    ring_community_id = None
    for idx, members in enumerate(communities):
        if len(members) < 3:
            continue
        _, purity = ring_purity(members)
        if purity >= 0.8:
            ring_community_id = idx
            break

    benign_community_id = None
    for idx, members in enumerate(communities):
        if len(members) < 3:
            continue
        _, purity = ring_purity(members)
        if purity > 0:
            continue
        if benign_tag(members) == "family":
            benign_community_id = idx
            break

    if ring_community_id is None or benign_community_id is None:
        raise RuntimeError("could not find both a pure ring community and a benign family community")

    print("=== Stage 7: agent investigator exit test ===")
    print(f"Ring community: #{ring_community_id} (size {len(communities[ring_community_id])})")
    print(f"Benign family community: #{benign_community_id} (size {len(communities[benign_community_id])})")

    client = _client()
    for label, cid in [("RING", ring_community_id), ("BENIGN FAMILY", benign_community_id)]:
        print(f"\n--- Investigating {label} community #{cid} ---")
        result = investigate_community(graph, communities, cid, client=client)
        print(f"is_ring={result.is_ring}  confidence={result.confidence:.2f}  action={result.recommended_action}")
        print(f"reasoning: {result.reasoning}")


if __name__ == "__main__":
    main()
