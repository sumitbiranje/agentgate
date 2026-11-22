"""One pass and one fail (at least) per built-in check type."""

from __future__ import annotations

from typing import Any

import pytest

from agentgate.checks import CheckContext, build_check, registered_types
from agentgate.config import CheckSpec
from agentgate.errors import ConfigError
from agentgate.models import Trace

from .conftest import make_config, make_trace


def run(spec: dict[str, Any], trace: Trace) -> tuple[bool, str]:
    config = make_config()
    check = build_check(CheckSpec.model_validate(spec), config)
    result = check.run(trace, config.cases[0], CheckContext(config), "x")
    return result.passed, result.message


SEQ = [("lookup", {"id": "1"}), ("search", {}), ("refund", {"id": "1", "amount": 5})]


@pytest.mark.parametrize(
    ("mode", "expected", "ok"),
    [
        ("in_order", ["lookup", "refund"], True),
        ("in_order", ["refund", "lookup"], False),
        ("exact", ["lookup", "search", "refund"], True),
        ("exact", ["lookup", "refund"], False),
        ("prefix", ["lookup", "search"], True),
        ("prefix", ["search"], False),
        ("contains_all", ["refund", "lookup"], True),
        ("contains_all", ["cancel"], False),
    ],
)
def test_tool_sequence(mode: str, expected: list[str], ok: bool) -> None:
    passed, msg = run(
        {"type": "tool_sequence", "expected": expected, "mode": mode}, make_trace(tools=SEQ)
    )
    assert passed is ok
    if not ok:
        assert "got [lookup, search, refund]" in msg


def test_tool_sequence_no_calls_message() -> None:
    passed, msg = run({"type": "tool_sequence", "expected": ["a"]}, make_trace())
    assert not passed
    assert "no tool calls" in msg


SCHEMA = {
    "type": "object",
    "required": ["id", "amount"],
    "properties": {"amount": {"type": "number", "maximum": 10}},
}


def test_tool_args_pass_and_fail() -> None:
    assert run({"type": "tool_args", "tool": "refund", "schema": SCHEMA}, make_trace(tools=SEQ))[0]
    bad = make_trace(tools=[("refund", {"id": "1", "amount": 50})])
    passed, msg = run({"type": "tool_args", "tool": "refund", "schema": SCHEMA}, bad)
    assert not passed
    assert "amount" in msg
    assert "50" in msg


def test_tool_args_when_first_last() -> None:
    trace = make_trace(
        tools=[("refund", {"id": "1", "amount": 1}), ("refund", {"id": "1", "amount": 99})]
    )
    spec = {"type": "tool_args", "tool": "refund", "schema": SCHEMA}
    assert run({**spec, "when": "first"}, trace)[0]
    assert not run({**spec, "when": "last"}, trace)[0]
    assert not run(spec, trace)[0]


def test_tool_args_not_called() -> None:
    spec = {"type": "tool_args", "tool": "refund", "schema": SCHEMA}
    passed, msg = run(spec, make_trace())
    assert not passed
    assert "never called" in msg
    assert run({**spec, "must_be_called": False}, make_trace())[0]


def test_tool_args_invalid_schema_is_config_error() -> None:
    with pytest.raises(ConfigError, match="invalid JSON schema"):
        build_check(
            CheckSpec.model_validate(
                {"type": "tool_args", "tool": "x", "schema": {"type": "nope"}}
            ),
            make_config(),
        )


def test_forbidden_tools() -> None:
    assert run({"type": "forbidden_tools", "tools": ["cancel"]}, make_trace(tools=SEQ))[0]
    passed, msg = run({"type": "forbidden_tools", "tools": ["refund"]}, make_trace(tools=SEQ))
    assert not passed
    assert "refund" in msg


def test_output_contains_modes() -> None:
    t = make_trace(output="Your Refund is on the way")
    assert run({"type": "output_contains", "values": ["refund", "way"]}, t)[0]
    passed, msg = run({"type": "output_contains", "values": ["refund", "credit"]}, t)
    assert not passed
    assert "'credit'" in msg
    assert run({"type": "output_contains", "values": ["credit", "way"], "mode": "any"}, t)[0]
    assert not run({"type": "output_contains", "values": ["qq", "zz"], "mode": "any"}, t)[0]
    assert not run({"type": "output_contains", "values": ["refund"], "case_sensitive": True}, t)[0]


def test_output_regex() -> None:
    t = make_trace(output="Refund RF-1042 issued")
    assert run({"type": "output_regex", "pattern": r"RF-\d+"}, t)[0]
    assert not run({"type": "output_regex", "pattern": r"rf-\d+"}, t)[0]
    assert run({"type": "output_regex", "pattern": r"rf-\d+", "flags": ["IGNORECASE"]}, t)[0]
    passed, msg = run({"type": "output_regex", "pattern": "issued", "should_match": False}, t)
    assert not passed
    assert "unexpectedly matched" in msg


def test_output_regex_invalid_pattern() -> None:
    with pytest.raises(ConfigError, match="invalid regex"):
        build_check(
            CheckSpec.model_validate({"type": "output_regex", "pattern": "("}), make_config()
        )


JSON_SCHEMA = {"type": "object", "required": ["status"], "properties": {"status": {"enum": ["ok"]}}}


@pytest.mark.parametrize(
    ("output", "ok"),
    [
        ('{"status": "ok"}', True),
        ('Here you go:\n```json\n{"status": "ok"}\n```', True),
        ('Result: {"status": "ok"} done', True),
        ('{"status": "bad"}', False),
        ("no json here", False),
    ],
)
def test_output_json_schema(output: str, ok: bool) -> None:
    assert (
        run({"type": "output_json_schema", "schema": JSON_SCHEMA}, make_trace(output=output))[0]
        is ok
    )


def test_output_json_schema_raw_mode() -> None:
    t = make_trace(output='Result: {"status": "ok"}')
    passed, msg = run({"type": "output_json_schema", "schema": JSON_SCHEMA, "extract": "raw"}, t)
    assert not passed
    assert "not valid JSON" in msg


def test_forbidden_content_scopes() -> None:
    t = make_trace(tools=[("email", {"to": "a@b.com"})], output="Done")
    spec = {"type": "forbidden_content", "patterns": [r"@b\.com"]}
    assert run(spec, t)[0]
    passed, msg = run({**spec, "scope": "tool_args"}, t)
    assert not passed
    assert "email args" in msg
    assert not run({**spec, "scope": "all"}, t)[0]
    assert not run({"type": "forbidden_content", "patterns": ["DONE"]}, t)[0]
    assert run({"type": "forbidden_content", "patterns": ["DONE"], "case_sensitive": True}, t)[0]


def test_max_steps_counts() -> None:
    t = make_trace(tools=SEQ)
    assert run({"type": "max_steps", "max": 3}, t)[0]
    passed, msg = run({"type": "max_steps", "max": 2}, t)
    assert not passed
    assert "3 tool_calls > max 2" in msg
    assert run({"type": "max_steps", "max": 1, "count": "llm_calls"}, t)[0]
    assert not run({"type": "max_steps", "max": 3, "count": "steps"}, t)[0]


def test_token_budget() -> None:
    t = make_trace(input_tokens=100, output_tokens=50)
    assert run({"type": "token_budget", "max_total": 150}, t)[0]
    passed, msg = run({"type": "token_budget", "max_total": 149, "max_output": 10}, t)
    assert not passed
    assert "total tokens 150 > 149" in msg
    assert "output tokens 50 > 10" in msg
    assert not run({"type": "token_budget", "max_input": 99}, t)[0]
    with pytest.raises(ConfigError, match="at least one"):
        build_check(CheckSpec.model_validate({"type": "token_budget"}), make_config())


def test_cost_budget() -> None:
    assert run({"type": "cost_budget", "max_usd": 0.01}, make_trace(cost=0.005))[0]
    assert not run({"type": "cost_budget", "max_usd": 0.01}, make_trace(cost=0.02))[0]
    passed, msg = run({"type": "cost_budget", "max_usd": 0.01}, make_trace(cost=None))
    assert not passed
    assert "cost unknown" in msg


def test_latency_budget() -> None:
    assert run({"type": "latency_budget", "max_ms": 1000}, make_trace(latency=1000))[0]
    passed, msg = run({"type": "latency_budget", "max_ms": 500}, make_trace(latency=1000))
    assert not passed
    assert "1000 ms > 500 ms" in msg
    assert not run({"type": "latency_budget", "max_ms": 500}, make_trace(latency=None))[0]


def test_no_error() -> None:
    assert run({"type": "no_error"}, make_trace())[0]
    passed, msg = run({"type": "no_error"}, make_trace(error="Timeout"))
    assert not passed
    assert "Timeout" in msg
    t = make_trace(tools=[("lookup", {})])
    t.steps[-1].error = "404"
    assert not run({"type": "no_error"}, t)[0]
    assert run({"type": "no_error", "allow_tool_errors": True}, t)[0]


def test_unknown_check_and_bad_params() -> None:
    with pytest.raises(ConfigError, match="unknown check type 'nope'"):
        build_check(CheckSpec.model_validate({"type": "nope"}), make_config())
    with pytest.raises(ConfigError, match="max"):
        build_check(CheckSpec.model_validate({"type": "max_steps", "max": "x"}), make_config())
    with pytest.raises(ConfigError, match="Extra inputs"):
        build_check(CheckSpec.model_validate({"type": "no_error", "typo": 1}), make_config())


def test_registry_lists_all_types() -> None:
    assert set(registered_types()) == {
        "tool_sequence",
        "tool_args",
        "forbidden_tools",
        "output_contains",
        "output_regex",
        "output_json_schema",
        "forbidden_content",
        "max_steps",
        "token_budget",
        "cost_budget",
        "latency_budget",
        "no_error",
        "python",
        "llm_judge",
    }


def test_register_duplicate_rejected() -> None:
    from agentgate.checks.base import register
    from agentgate.checks.builtin import NoErrorCheck

    with pytest.raises(ValueError, match="already registered"):
        register(NoErrorCheck)
