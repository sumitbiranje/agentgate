"""Langfuse trace export -> ``Trace`` (experimental, best effort).

Expects trace objects with nested ``observations``, as returned by Langfuse's public
API for a single trace. ``GENERATION`` observations become LLM steps; ``TOOL``
observations (and ``SPAN``s with ``metadata.tool`` truthy) become tool calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agentgate.errors import TraceFormatError
from agentgate.models import Step, StepType, Trace, Usage


def _ts(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def _dur(obs: dict[str, Any]) -> float | None:
    start, end = _ts(obs.get("startTime")), _ts(obs.get("endTime"))
    return end - start if start is not None and end is not None else None


def _tokens(obs: dict[str, Any]) -> tuple[int, int]:
    details = obs.get("usageDetails") or {}
    usage = obs.get("usage") or {}
    inp = details.get("input", usage.get("input", usage.get("promptTokens", 0)))
    out = details.get("output", usage.get("output", usage.get("completionTokens", 0)))
    return int(inp or 0), int(out or 0)


def trace_from_langfuse(obj: Any, where: str = "<langfuse>") -> Trace:
    if not isinstance(obj, dict) or "observations" not in obj:
        raise TraceFormatError(f"{where}: expected a Langfuse trace with 'observations'")
    meta = obj.get("metadata") or {}
    steps: list[Step] = []
    total_cost = 0.0
    any_cost = False
    observations = sorted(obj["observations"], key=lambda o: str(o.get("startTime", "")))
    for obs in observations:
        kind = obs.get("type")
        if kind == "GENERATION":
            inp, out = _tokens(obs)
            if obs.get("calculatedTotalCost") is not None:
                total_cost += float(obs["calculatedTotalCost"])
                any_cost = True
            steps.append(
                Step(
                    type=StepType.LLM,
                    name=str(obs.get("model") or obs.get("name") or ""),
                    input_tokens=inp,
                    output_tokens=out,
                    latency_ms=_dur(obs),
                    error=obs.get("statusMessage") if obs.get("level") == "ERROR" else None,
                )
            )
        elif kind == "TOOL" or (kind == "SPAN" and (obs.get("metadata") or {}).get("tool")):
            args = obs.get("input")
            steps.append(
                Step(
                    type=StepType.TOOL_CALL,
                    name=str(obs.get("name") or ""),
                    arguments=args
                    if isinstance(args, dict)
                    else ({} if args is None else {"value": args}),
                    result=obs.get("output"),
                    latency_ms=_dur(obs),
                    error=obs.get("statusMessage") if obs.get("level") == "ERROR" else None,
                )
            )
    output = obj.get("output")
    if isinstance(output, dict):
        output = output.get("content") or output.get("text") or output
    latency = obj.get("latency")  # seconds in Langfuse's API
    return Trace(
        case_id=str(meta.get("case_id") or obj.get("name") or obj.get("id")),
        run_id=str(obj.get("id", "")),
        attempt=int(meta.get("attempt", 0)),
        input=obj.get("input"),
        steps=steps,
        final_output=output if isinstance(output, str) else ("" if output is None else str(output)),
        usage=Usage(
            input_tokens=sum(s.input_tokens for s in steps),
            output_tokens=sum(s.output_tokens for s in steps),
            cost_usd=total_cost if any_cost else obj.get("totalCost"),
        ),
        latency_ms=float(latency) * 1000 if latency is not None else None,
        metadata={"source": "langfuse"},
    )
