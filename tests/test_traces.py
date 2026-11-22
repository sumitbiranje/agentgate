from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgate.errors import TraceFormatError
from agentgate.models import StepType
from agentgate.traces import dump_jsonl, load_file, load_traces
from agentgate.traces.otel import any_value

from .conftest import DEMO, make_trace

DATA = Path(__file__).parent / "data"


def write(path: Path, lines: list[object]) -> Path:
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return path


def test_native_roundtrip(tmp_path: Path) -> None:
    traces = [make_trace("a", [("t", {"x": 1})], "out", attempt=i) for i in range(3)]
    dump_jsonl(traces, tmp_path / "sub" / "t.jsonl")
    loaded = load_traces([tmp_path]).traces
    assert [t.model_dump() for t in loaded] == [t.model_dump() for t in traces]


def test_native_unknown_keys_go_to_metadata(tmp_path: Path) -> None:
    p = write(tmp_path / "t.jsonl", [{"case_id": "a", "agent_version": "2", "metadata": {"k": 1}}])
    (t,) = load_file(p)
    assert t.metadata == {"agent_version": "2", "k": 1}


def test_native_errors_name_file_and_line(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    p.write_text('{"case_id": "a"}\n\n{not json}\n')
    with pytest.raises(TraceFormatError, match=r"t\.jsonl:3: invalid JSON"):
        load_traces([p])
    write(p, [{"run_id": "x"}])
    with pytest.raises(TraceFormatError, match="case_id"):
        load_traces([p])
    write(p, [{"schema": "other/v9", "case_id": "a"}])
    with pytest.raises(TraceFormatError, match="unsupported schema"):
        load_traces([p])
    write(p, [[1, 2]])
    with pytest.raises(TraceFormatError, match="expected a JSON object"):
        load_traces([p], "native")


def test_lenient_skips_bad_lines(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    p.write_text('{"case_id": "a"}\n{oops\n{"case_id": "b"}\n')
    res = load_traces([p], lenient=True)
    assert [t.case_id for t in res.traces] == ["a", "b"]
    assert len(res.warnings) == 1


def test_native_json_array_file(tmp_path: Path) -> None:
    p = tmp_path / "t.json"
    p.write_text(json.dumps([{"case_id": "a"}, {"case_id": "b"}]))
    assert [t.case_id for t in load_traces([p]).traces] == ["a", "b"]
    p.write_text("{broken")
    with pytest.raises(TraceFormatError, match="invalid JSON"):
        load_traces([p])


def test_loader_paths_and_ids(tmp_path: Path) -> None:
    with pytest.raises(TraceFormatError, match="not found"):
        load_traces([tmp_path / "missing"])
    (tmp_path / "empty").mkdir()
    with pytest.raises(TraceFormatError, match="no trace files"):
        load_traces([tmp_path / "empty"])
    p = write(
        tmp_path / "t.jsonl",
        [{"case_id": "a"}, {"case_id": "a", "run_id": "r"}, {"case_id": "a", "run_id": "r"}],
    )
    res = load_traces([p], variant="cand")
    assert res.traces[0].run_id == "a#0#0"
    assert all(t.variant == "cand" for t in res.traces)
    assert any("duplicate run_id" in w for w in res.warnings)


def test_otel_parsing() -> None:
    traces = load_traces([DATA / "otel_small.json"]).traces
    by_id = {t.run_id: t for t in traces}
    t = by_id["t1"]
    assert t.case_id == "refund"  # span attr beats resource attr
    assert t.attempt == 2
    assert [s.type for s in t.steps] == [StepType.LLM, StepType.TOOL_CALL, StepType.LLM]
    assert t.steps[0].name == "m-resp"
    assert t.steps[1].arguments == {"id": "7"}
    assert t.steps[1].result == {"ok": True}
    assert t.steps[2].input_tokens == 150  # legacy attribute names
    assert t.usage.total_tokens == 290
    assert t.final_output == "Refunded."
    assert t.latency_ms == pytest.approx(2500)
    assert t.error is None
    other = by_id["t2"]
    assert other.case_id == "from-resource"
    assert other.error == "boom"
    assert other.steps == []


def test_otel_any_value() -> None:
    assert any_value({"boolValue": True}) is True
    assert any_value({"doubleValue": 1.5}) == 1.5
    assert any_value({"arrayValue": {"values": [{"stringValue": "a"}]}}) == ["a"]
    assert any_value({"kvlistValue": {"values": [{"key": "k", "value": {"intValue": "3"}}]}}) == {
        "k": 3
    }
    assert any_value({"bytesValue": "AA=="}) == "AA=="
    assert any_value({}) is None


def test_otel_missing_trace_id(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "s"}]}]}]}))
    with pytest.raises(TraceFormatError, match="no traceId"):
        load_traces([p])


def test_otel_jsonl_lines(tmp_path: Path) -> None:
    doc = json.loads((DATA / "otel_small.json").read_text())
    p = tmp_path / "spans.jsonl"
    p.write_text(json.dumps(doc) + "\n")
    assert len(load_traces([p]).traces) == 2


def test_demo_otel_equals_native() -> None:
    """The OTLP export of the demo baseline must load to the same verdict-relevant data."""
    native = load_traces([DEMO / "runs" / "baseline"]).traces
    otel = load_traces([DEMO / "runs" / "baseline-otel"]).traces
    key = lambda t: (t.case_id, t.attempt)  # noqa: E731
    native.sort(key=key)
    otel.sort(key=key)
    assert len(native) == len(otel) == 50
    for a, b in zip(native, otel, strict=True):
        assert (a.case_id, a.attempt, a.run_id) == (b.case_id, b.attempt, b.run_id)
        assert a.tool_names == b.tool_names
        assert [s.arguments for s in a.tool_calls] == [s.arguments for s in b.tool_calls]
        assert a.final_output == b.final_output
        assert a.usage.total_tokens == b.usage.total_tokens
        assert a.latency_ms == pytest.approx(b.latency_ms, abs=0.01)


def test_langfuse_parsing() -> None:
    (t,) = load_traces([DATA / "langfuse_small.json"]).traces
    assert t.case_id == "refund"
    assert t.attempt == 1
    assert t.final_output == "Done."
    assert t.latency_ms == pytest.approx(2500)
    assert [s.name for s in t.steps] == ["m", "lookup", "issue_refund", "m"]  # LLM steps: model
    assert t.steps[0].latency_ms == pytest.approx(800)
    assert t.steps[2].arguments == {"value": "raw"}
    assert t.steps[2].error == "declined"
    assert t.steps[3].input_tokens == 5
    assert t.steps[3].latency_ms is None
    assert t.usage.cost_usd == pytest.approx(0.0015)


def test_langfuse_requires_observations(tmp_path: Path) -> None:
    p = tmp_path / "lf.json"
    p.write_text(json.dumps({"id": "x"}))
    with pytest.raises(TraceFormatError, match="observations"):
        load_traces([p], "langfuse")
