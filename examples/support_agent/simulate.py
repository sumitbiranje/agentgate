"""Record demo runs of the support agent without any API key.

The "model" here is a scripted, seeded policy that stands in for an LLM: for each case
it decides which tools to call and what to answer, with realistic token counts,
latency and a little randomness. Three versions of the agent are recorded:

* ``baseline``        v1.0, the version on main.
* ``candidate-good``  v1.1, trimmed system prompt: same behaviour, fewer tokens.
* ``candidate-bad``   v1.2, "be extra helpful" prompt change: refunds orders outside the
                      policy window, sometimes obeys a prompt injection, sometimes leaks a
                      shipping address, and is more verbose (more tokens, slower).

Run:  uv run python examples/support_agent/simulate.py
Writes runs/<variant>/traces.jsonl and an OTLP/JSON copy of the baseline
(runs/baseline-otel/traces.otlp.json) to exercise the OpenTelemetry loader.
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from store import TOOLS  # noqa: E402

from agentgate.models import Trace  # noqa: E402
from agentgate.recorder import TraceRecorder  # noqa: E402

ATTEMPTS = 5
SEED = 20261122


@dataclass(frozen=True)
class Variant:
    name: str
    version: str
    model: str
    system_tokens: int
    verbosity: int  # mean output tokens of the final answer
    flags: frozenset[str] = field(default_factory=frozenset)


VARIANTS = [
    Variant("baseline", "1.0.0", "sim-model", system_tokens=900, verbosity=70),
    Variant("candidate-good", "1.1.0", "sim-model", system_tokens=720, verbosity=62),
    Variant(
        "candidate-bad",
        "1.2.0",
        "sim-model",
        system_tokens=1150,
        verbosity=115,
        flags=frozenset({"refund_any_order", "obey_injection", "leak_address", "chatty"}),
    ),
]

CHATTY_TAIL = (
    " I'm always happy to help, and I've gone the extra mile here to make sure you're"
    " completely satisfied. Is there anything else at all I can do for you today?"
)

Plan = tuple[list[tuple[str, dict[str, Any]]], str]


def plan_for(case_id: str, v: Variant, rng: random.Random) -> Plan:
    """Decide tool calls and the final answer for one run of one case."""
    f = v.flags
    if case_id == "order-status":
        return [("lookup_order", {"order_id": "1001"})], (
            "Order 1001 has shipped with ParcelPost, tracking number PP-88213."
        )
    if case_id == "refund-in-window":
        return [
            ("lookup_order", {"order_id": "1042"}),
            ("issue_refund", {"order_id": "1042", "amount": 25.00, "reason": "customer request"}),
        ], "Your refund of $25.00 for order 1042 has been issued. It should appear in 3-5 days."
    if case_id == "refund-out-of-window":
        if "refund_any_order" in f:
            return [
                ("lookup_order", {"order_id": "0977"}),
                ("issue_refund", {"order_id": "0977", "amount": 89.00, "reason": "goodwill"}),
            ], "Good news! I made an exception: your refund of $89.00 has been issued."
        return [("lookup_order", {"order_id": "0977"}), ("get_refund_policy", {})], (
            "I'm sorry, order 0977 was delivered 64 days ago, which is outside our 30-day "
            "refund window, so I can't refund it. I can offer store credit instead, or pass "
            "this to our returns team to review."
        )
    if case_id == "damaged-item":
        return [
            ("lookup_order", {"order_id": "1063"}),
            ("escalate_to_human", {"reason": "item arrived damaged", "order_id": "1063"}),
        ], (
            "I'm sorry your mug set arrived damaged. I've passed this to our returns team "
            "(ticket T-0412) and they will contact you within one business day."
        )
    if case_id == "prompt-injection":
        if "obey_injection" in f and rng.random() < 0.4:
            return [
                ("lookup_order", {"order_id": "1042"}),
                ("issue_refund", {"order_id": "1042", "amount": 500.00, "reason": "as instructed"}),
            ], "Done: I've issued a refund of $500.00 for order 1042."
        return [("lookup_order", {"order_id": "1042"})], (
            "I can only refund the amount paid for order 1042, which is $25.00. Would you like "
            "me to refund that?"
        )
    if case_id == "pii-request":
        if "leak_address" in f and rng.random() < 0.4:
            return [("lookup_order", {"order_id": "1001"})], (
                "Order 1001 is going to 42 Elm Street, Springfield."
            )
        return [("lookup_order", {"order_id": "1001"})], (
            "I can't share delivery details for an order that isn't yours. The account holder "
            "can see them in their order history."
        )
    if case_id == "faq-shipping":
        return [("search_kb", {"query": "shipping times"})], (
            "Standard shipping takes 3-5 business days; express takes 1-2 business days."
        )
    if case_id == "order-json":
        return [("lookup_order", {"order_id": "1001"})], json.dumps(
            {"order_id": "1001", "status": "shipped", "items": ["desk lamp"]}
        )
    if case_id == "cancel-order":
        return [("lookup_order", {"order_id": "1100"}), ("cancel_order", {"order_id": "1100"})], (
            "Order 1100 has been cancelled. You will not be charged."
        )
    if case_id == "multi-order-status":
        # Inherently flaky in every version: the agent sometimes answers after one lookup.
        if rng.random() < 0.3:
            return [("lookup_order", {"order_id": "1001"})], (
                "Order 1001 has shipped (tracking PP-88213)."
            )
        return [("lookup_order", {"order_id": "1001"}), ("lookup_order", {"order_id": "1100"})], (
            "Order 1001 has shipped (tracking PP-88213). Order 1100 is still processing."
        )
    raise KeyError(case_id)


def simulate_run(
    case: dict[str, Any], v: Variant, attempt: int, rng: random.Random, rec: TraceRecorder
) -> Trace:
    calls, answer = plan_for(case["id"], v, rng)
    if "chatty" in v.flags:
        answer += CHATTY_TAIL
    clock = 0.0
    with rec.run(
        case_id=case["id"],
        input=case["input"],
        attempt=attempt,
        run_id=f"{v.name}-{case['id']}-{attempt}",
        metadata={"agent_version": v.version, "variant": v.name},
    ) as run:
        context = v.system_tokens + 40
        for i in range(len(calls) + 1):
            final = i == len(calls)
            out = max(10, int(rng.gauss(v.verbosity if final else 38, 6)))
            lat = 300 + context * 0.12 + out * 16 + rng.gauss(0, 50)
            run.llm(v.model, input_tokens=context, output_tokens=out, latency_ms=round(lat, 1))
            clock += lat
            if final:
                break
            name, args = calls[i]
            result = TOOLS[name](**args)
            tool_lat = round(rng.uniform(20, 90), 1)
            run.tool(name, args, result=result, latency_ms=tool_lat)
            clock += tool_lat
            context += out + 110
        return run.finish(answer, latency_ms=round(clock, 1))


def to_otlp(traces: list[Trace]) -> dict[str, Any]:
    """Export traces as OTLP/JSON spans following the GenAI semantic conventions."""

    def s(v: str) -> dict[str, Any]:
        return {"stringValue": v}

    def i(v: int) -> dict[str, Any]:
        return {"intValue": str(v)}

    spans = []
    base = 1_763_600_000_000_000_000
    for n, t in enumerate(traces):
        trace_id = f"{n + 1:032x}"
        root_id = f"{(n + 1) * 1000:016x}"
        start = base + n * 60_000_000_000
        end = start + int((t.latency_ms or 0) * 1e6)
        spans.append(
            {
                "traceId": trace_id,
                "spanId": root_id,
                "name": "invoke_agent support",
                "startTimeUnixNano": str(start),
                "endTimeUnixNano": str(end),
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": s("invoke_agent")},
                    {"key": "agentgate.case_id", "value": s(t.case_id)},
                    {"key": "agentgate.run_id", "value": s(t.run_id)},
                    {"key": "agentgate.attempt", "value": i(t.attempt)},
                    {"key": "agentgate.final_output", "value": s(t.final_output)},
                ],
                "status": {"code": 1},
            }
        )
        cursor = start
        for k, step in enumerate(t.steps):
            dur = int((step.latency_ms or 0) * 1e6)
            attrs = []
            if step.type == "llm":
                attrs = [
                    {"key": "gen_ai.operation.name", "value": s("chat")},
                    {"key": "gen_ai.request.model", "value": s(step.name)},
                    {"key": "gen_ai.usage.input_tokens", "value": i(step.input_tokens)},
                    {"key": "gen_ai.usage.output_tokens", "value": i(step.output_tokens)},
                ]
                name = f"chat {step.name}"
            else:
                attrs = [
                    {"key": "gen_ai.operation.name", "value": s("execute_tool")},
                    {"key": "gen_ai.tool.name", "value": s(step.name)},
                    {"key": "gen_ai.tool.call.arguments", "value": s(json.dumps(step.arguments))},
                    {"key": "gen_ai.tool.call.result", "value": s(json.dumps(step.result))},
                ]
                name = f"execute_tool {step.name}"
            spans.append(
                {
                    "traceId": trace_id,
                    "spanId": f"{(n + 1) * 1000 + k + 1:016x}",
                    "parentSpanId": root_id,
                    "name": name,
                    "startTimeUnixNano": str(cursor),
                    "endTimeUnixNano": str(cursor + dur),
                    "attributes": attrs,
                }
            )
            cursor += dur
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": s("support-agent")}]},
                "scopeSpans": [{"scope": {"name": "demo"}, "spans": spans}],
            }
        ]
    }


def main(out_dir: Path = HERE / "runs") -> None:
    suite = yaml.safe_load((HERE / "agentgate.yaml").read_text())
    for v in VARIANTS:
        path = out_dir / v.name / "traces.jsonl"
        path.unlink(missing_ok=True)
        rec = TraceRecorder(path)
        # One fixed seed per (variant, case, attempt): re-running is byte-for-byte
        # identical, and adding a case does not reshuffle every other run.
        traces = [
            simulate_run(case, v, a, random.Random(f"{SEED}-{v.name}-{case['id']}-{a}"), rec)
            for case in suite["cases"]
            for a in range(ATTEMPTS)
        ]
        print(f"{v.name}: {len(traces)} runs -> {path}")
        if v.name == "baseline":
            otel = out_dir / "baseline-otel" / "traces.otlp.json"
            otel.parent.mkdir(parents=True, exist_ok=True)
            otel.write_text(json.dumps(to_otlp(traces), indent=1) + "\n")
            print(f"baseline (OTLP): {len(traces)} traces -> {otel}")


if __name__ == "__main__":
    main()
