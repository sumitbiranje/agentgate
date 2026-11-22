from __future__ import annotations

from pathlib import Path

import pytest

from agentgate.recorder import TraceRecorder
from agentgate.traces import load_traces


def test_recorder_writes_loadable_traces(tmp_path: Path) -> None:
    rec = TraceRecorder(tmp_path / "out" / "t.jsonl")
    with rec.run("c1", input="hi", attempt=1, run_id="r1", metadata={"v": "1"}) as run:
        run.llm("m", input_tokens=10, output_tokens=5, latency_ms=100, cost_usd=0.001)
        run.tool("lookup", {"id": 1}, result={"ok": True}, latency_ms=20)
        run.llm("m", input_tokens=20, output_tokens=7, cost_usd=0.002)
        run.finish("done", latency_ms=150)
    with rec.run("c2") as run:  # latency measured automatically
        run.trace.final_output = "x"
    with pytest.raises(ValueError, match="boom"), rec.run("c3"):
        raise ValueError("boom")
    traces = {t.case_id: t for t in load_traces([tmp_path]).traces}
    t = traces["c1"]
    assert (t.attempt, t.run_id, t.final_output, t.latency_ms) == (1, "r1", "done", 150)
    assert t.usage.total_tokens == 42
    assert t.usage.cost_usd == pytest.approx(0.003)
    assert t.tool_names == ["lookup"]
    assert traces["c2"].latency_ms is not None
    assert traces["c3"].error == "ValueError: boom"
