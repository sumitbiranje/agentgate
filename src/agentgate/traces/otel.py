"""OpenTelemetry GenAI spans (OTLP/JSON export) -> ``Trace``.

Reads the GenAI semantic conventions (``gen_ai.*`` attributes). Those conventions are
still evolving, so the parser accepts a few older attribute spellings too. Each OTel
trace (``traceId``) becomes one agentgate trace; spans become steps in start order.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from agentgate.errors import TraceFormatError
from agentgate.models import Step, StepType, Trace, Usage

LLM_OPERATIONS = {"chat", "text_completion", "generate_content"}
TOOL_OPERATIONS = {"execute_tool"}
STATUS_ERROR = 2


def any_value(v: dict[str, Any]) -> Any:
    """Decode an OTLP ``AnyValue``."""
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        return int(v["intValue"])  # int64 is encoded as a string in OTLP/JSON
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "boolValue" in v:
        return bool(v["boolValue"])
    if "arrayValue" in v:
        return [any_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return attributes(v["kvlistValue"].get("values", []))
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def attributes(kvs: list[dict[str, Any]]) -> dict[str, Any]:
    return {kv["key"]: any_value(kv.get("value", {})) for kv in kvs if "key" in kv}


def _first(attrs: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if attrs.get(k) is not None:
            return attrs[k]
    return None


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _text_from_messages(value: Any) -> str:
    """Extract text from ``gen_ai.output.messages`` ([{role, parts:[{type, content}]}])."""
    messages = _maybe_json(value)
    if isinstance(messages, str):
        return messages
    texts: list[str] = []
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            for part in msg.get("parts", []):
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("content", "")))
    return "\n".join(t for t in texts if t)


def _ns(span: dict[str, Any], key: str) -> int:
    return int(span.get(key) or 0)


def _flatten(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Return spans with resource attributes merged in (span attributes win)."""
    spans = []
    for rs in doc.get("resourceSpans", []):
        res_attrs = attributes(rs.get("resource", {}).get("attributes", []))
        for ss in rs.get("scopeSpans", rs.get("instrumentationLibrarySpans", [])):
            for span in ss.get("spans", []):
                merged = {**res_attrs, **attributes(span.get("attributes", []))}
                spans.append({**span, "_attrs": merged})
    return spans


def _span_to_step(span: dict[str, Any]) -> Step | None:
    a = span["_attrs"]
    op = a.get("gen_ai.operation.name")
    dur_ms = (_ns(span, "endTimeUnixNano") - _ns(span, "startTimeUnixNano")) / 1e6
    failed = span.get("status", {}).get("code") in (STATUS_ERROR, "STATUS_CODE_ERROR")
    error = (
        (span.get("status", {}).get("message") or a.get("error.type") or "error")
        if failed
        else None
    )
    if op in LLM_OPERATIONS:
        return Step(
            type=StepType.LLM,
            name=str(_first(a, "gen_ai.response.model", "gen_ai.request.model") or ""),
            input_tokens=int(
                _first(a, "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens") or 0
            ),
            output_tokens=int(
                _first(a, "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens") or 0
            ),
            latency_ms=dur_ms,
            error=error,
        )
    if op in TOOL_OPERATIONS:
        args = _maybe_json(_first(a, "gen_ai.tool.call.arguments", "gen_ai.tool.arguments"))
        return Step(
            type=StepType.TOOL_CALL,
            name=str(
                a.get("gen_ai.tool.name") or span.get("name", "").removeprefix("execute_tool ")
            ),
            arguments=args if isinstance(args, dict) else ({} if args is None else {"value": args}),
            result=_maybe_json(_first(a, "gen_ai.tool.call.result", "gen_ai.tool.result")),
            latency_ms=dur_ms,
            error=error,
        )
    return None


def traces_from_otlp(docs: list[dict[str, Any]], where: str = "<otel>") -> list[Trace]:
    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        for span in _flatten(doc):
            tid = span.get("traceId")
            if not tid:
                raise TraceFormatError(f"{where}: span {span.get('name')!r} has no traceId")
            by_trace[tid].append(span)

    traces = []
    for tid, spans in by_trace.items():
        spans.sort(key=lambda s: (_ns(s, "startTimeUnixNano"), _ns(s, "endTimeUnixNano")))
        ids = {s.get("spanId") for s in spans}
        roots = [s for s in spans if not s.get("parentSpanId") or s.get("parentSpanId") not in ids]
        root = roots[0]

        ordered = [root, *spans]

        def find(key: str, ordered: list[dict[str, Any]] = ordered) -> Any:
            for s in ordered:
                if s["_attrs"].get(key) is not None:
                    return s["_attrs"][key]
            return None

        steps = [st for st in (_span_to_step(s) for s in spans) if st is not None]
        llm_spans = [s for s in spans if s["_attrs"].get("gen_ai.operation.name") in LLM_OPERATIONS]
        final = find("agentgate.final_output")
        if final is None and llm_spans:
            final = _text_from_messages(llm_spans[-1]["_attrs"].get("gen_ai.output.messages"))
        root_failed = root.get("status", {}).get("code") in (STATUS_ERROR, "STATUS_CODE_ERROR")
        cost = find("agentgate.cost_usd")
        traces.append(
            Trace(
                case_id=str(
                    find("agentgate.case_id")
                    or find("gen_ai.conversation.id")
                    or root.get("name", tid)
                ),
                run_id=str(find("agentgate.run_id") or tid),
                attempt=int(find("agentgate.attempt") or 0),
                input=_maybe_json(find("agentgate.input")),
                steps=steps,
                final_output=str(final or ""),
                usage=Usage(
                    input_tokens=sum(s.input_tokens for s in steps),
                    output_tokens=sum(s.output_tokens for s in steps),
                    cost_usd=float(cost) if cost is not None else None,
                ),
                latency_ms=(_ns(root, "endTimeUnixNano") - _ns(root, "startTimeUnixNano")) / 1e6,
                error=(root.get("status", {}).get("message") or "error") if root_failed else None,
                metadata={"source": "otel", "trace_id": tid},
            )
        )
    traces.sort(key=lambda t: (t.case_id, t.attempt, t.run_id))
    return traces
