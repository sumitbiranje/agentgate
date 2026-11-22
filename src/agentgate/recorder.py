"""Minimal helper for agents to write native agentgate traces.

    rec = TraceRecorder("runs/candidate/traces.jsonl")
    with rec.run(case_id="refund-in-window", input=prompt, attempt=0) as run:
        run.llm("model-x", input_tokens=812, output_tokens=64)
        run.tool("lookup_order", {"order_id": "1042"}, result={...})
        run.finish("Your refund has been issued.")

Latency is measured with a monotonic clock unless passed explicitly (useful for
simulations and for replaying timings from another system).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agentgate.models import TRACE_SCHEMA, Step, StepType, Trace, Usage


class RunRecorder:
    def __init__(
        self, case_id: str, input: Any, attempt: int, run_id: str | None, metadata: dict[str, Any]
    ) -> None:
        self.trace = Trace(
            case_id=case_id,
            input=input,
            attempt=attempt,
            run_id=run_id or uuid.uuid4().hex[:12],
            metadata=metadata,
        )
        self._start = time.monotonic()
        self._cost: float | None = None

    def llm(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float | None = None,
        cost_usd: float | None = None,
    ) -> None:
        self.trace.steps.append(
            Step(
                type=StepType.LLM,
                name=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
            )
        )
        if cost_usd is not None:
            self._cost = (self._cost or 0.0) + cost_usd

    def tool(
        self,
        name: str,
        arguments: dict[str, Any],
        result: Any = None,
        error: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        self.trace.steps.append(
            Step(
                type=StepType.TOOL_CALL,
                name=name,
                arguments=arguments,
                result=result,
                error=error,
                latency_ms=latency_ms,
            )
        )

    def finish(
        self, output: str, latency_ms: float | None = None, error: str | None = None
    ) -> Trace:
        t = self.trace
        t.final_output = output
        t.error = error
        t.usage = Usage(
            input_tokens=sum(s.input_tokens for s in t.steps),
            output_tokens=sum(s.output_tokens for s in t.steps),
            cost_usd=self._cost,
        )
        t.latency_ms = (
            latency_ms if latency_ms is not None else (time.monotonic() - self._start) * 1000
        )
        return t


class TraceRecorder:
    """Appends one JSON line per finished run."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, trace: Trace) -> None:
        record = {"schema": TRACE_SCHEMA, **trace.model_dump(mode="json")}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @contextmanager
    def run(
        self,
        case_id: str,
        input: Any = None,
        attempt: int = 0,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[RunRecorder]:
        rec = RunRecorder(case_id, input, attempt, run_id, metadata or {})
        try:
            yield rec
        except Exception as exc:
            rec.finish(rec.trace.final_output, error=f"{type(exc).__name__}: {exc}")
            self.write(rec.trace)
            raise
        if rec.trace.latency_ms is None:
            rec.finish(rec.trace.final_output)
        self.write(rec.trace)
