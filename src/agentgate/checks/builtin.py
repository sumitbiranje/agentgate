"""Deterministic built-in checks.

These decide every verdict that does not need judgement: what the agent called, with
which arguments, what it said, and what it cost. They are cheap, exact and repeatable,
so they should carry most of a suite.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentgate.checks.base import Check, CheckContext, CheckOutcome, register
from agentgate.config import CaseSpec
from agentgate.models import Trace


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_schema(schema: dict[str, Any]) -> dict[str, Any]:
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ValueError(f"invalid JSON schema: {exc.message}") from exc
    return schema


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc


_FLAG_NAMES = {"IGNORECASE": re.IGNORECASE, "MULTILINE": re.MULTILINE, "DOTALL": re.DOTALL}


def _fmt_seq(names: list[str]) -> str:
    return "[" + ", ".join(names) + "]" if names else "[] (no tool calls)"


# --------------------------------------------------------------------------- tools


class ToolSequenceParams(_Params):
    expected: list[str] = Field(min_length=1)
    mode: Literal["exact", "in_order", "contains_all", "prefix"] = "in_order"


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(any(h == n for h in it) for n in needle)


@register
class ToolSequenceCheck(Check):
    """Tool-call names and order. ``in_order`` allows extra calls in between."""

    type_name = "tool_sequence"
    Params = ToolSequenceParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: ToolSequenceParams = self.params  # type: ignore[assignment]
        actual = trace.tool_names
        if p.mode == "exact":
            ok = actual == p.expected
        elif p.mode == "prefix":
            ok = actual[: len(p.expected)] == p.expected
        elif p.mode == "contains_all":
            ok = all(name in actual for name in p.expected)
        else:
            ok = _is_subsequence(p.expected, actual)
        msg = "" if ok else f"expected {p.mode} {_fmt_seq(p.expected)}, got {_fmt_seq(actual)}"
        return CheckOutcome(ok, msg, details={"actual": actual})


class ToolArgsParams(_Params):
    tool: str
    schema_: dict[str, Any] = Field(alias="schema")
    when: Literal["all", "first", "last"] = "all"
    must_be_called: bool = True

    @field_validator("schema_")
    @classmethod
    def _schema_ok(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _validate_schema(v)


@register
class ToolArgsCheck(Check):
    """Validate a tool's arguments against a JSON Schema."""

    type_name = "tool_args"
    Params = ToolArgsParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: ToolArgsParams = self.params  # type: ignore[assignment]
        calls = [s for s in trace.tool_calls if s.name == p.tool]
        if not calls:
            if p.must_be_called:
                return CheckOutcome(False, f"tool {p.tool!r} was never called")
            return CheckOutcome(True, f"tool {p.tool!r} not called (allowed)")
        if p.when == "first":
            calls = calls[:1]
        elif p.when == "last":
            calls = calls[-1:]
        validator = jsonschema.Draft202012Validator(p.schema_)
        for idx, call in enumerate(calls):
            errors = sorted(validator.iter_errors(call.arguments), key=lambda e: list(e.path))
            if errors:
                err = errors[0]
                where = "/".join(str(x) for x in err.path) or "<args>"
                return CheckOutcome(
                    False,
                    f"{p.tool} call #{idx + 1}: {where}: {err.message}",
                    details={"arguments": call.arguments},
                )
        return CheckOutcome(True)


class ForbiddenToolsParams(_Params):
    tools: list[str] = Field(min_length=1)


@register
class ForbiddenToolsCheck(Check):
    type_name = "forbidden_tools"
    Params = ForbiddenToolsParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: ForbiddenToolsParams = self.params  # type: ignore[assignment]
        used = [n for n in trace.tool_names if n in p.tools]
        if used:
            return CheckOutcome(
                False, f"called forbidden tool(s): {', '.join(dict.fromkeys(used))}"
            )
        return CheckOutcome(True)


# --------------------------------------------------------------------------- output


class OutputContainsParams(_Params):
    values: list[str] = Field(min_length=1)
    mode: Literal["all", "any"] = "all"
    case_sensitive: bool = False


@register
class OutputContainsCheck(Check):
    type_name = "output_contains"
    Params = OutputContainsParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: OutputContainsParams = self.params  # type: ignore[assignment]
        out = trace.final_output if p.case_sensitive else trace.final_output.lower()
        hits = [v for v in p.values if (v if p.case_sensitive else v.lower()) in out]
        missing = [v for v in p.values if v not in hits]
        ok = not missing if p.mode == "all" else bool(hits)
        if ok:
            return CheckOutcome(True)
        if p.mode == "all":
            return CheckOutcome(False, f"output missing: {', '.join(repr(m) for m in missing)}")
        return CheckOutcome(False, f"output contains none of: {', '.join(map(repr, p.values))}")


class OutputRegexParams(_Params):
    pattern: str
    should_match: bool = True
    flags: list[Literal["IGNORECASE", "MULTILINE", "DOTALL"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> OutputRegexParams:
        _compile(self.pattern)
        return self

    def compiled(self) -> re.Pattern[str]:
        flags = 0
        for f in self.flags:
            flags |= _FLAG_NAMES[f]
        return _compile(self.pattern, flags)


@register
class OutputRegexCheck(Check):
    type_name = "output_regex"
    Params = OutputRegexParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: OutputRegexParams = self.params  # type: ignore[assignment]
        matched = p.compiled().search(trace.final_output) is not None
        if matched == p.should_match:
            return CheckOutcome(True)
        verb = "did not match" if p.should_match else "unexpectedly matched"
        return CheckOutcome(False, f"output {verb} /{p.pattern}/")


class OutputJsonSchemaParams(_Params):
    schema_: dict[str, Any] = Field(alias="schema")
    extract: Literal["auto", "raw"] = "auto"

    @field_validator("schema_")
    @classmethod
    def _schema_ok(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _validate_schema(v)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str, mode: str = "auto") -> Any:
    """Parse JSON from agent output. ``auto`` also tries fenced blocks and the first
    balanced ``{...}`` / ``[...]`` span, because agents often wrap JSON in prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if mode == "raw":
            raise
    for block in _FENCE.findall(text):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                continue
            return value
    raise json.JSONDecodeError("no JSON value found", text, 0)


@register
class OutputJsonSchemaCheck(Check):
    type_name = "output_json_schema"
    Params = OutputJsonSchemaParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: OutputJsonSchemaParams = self.params  # type: ignore[assignment]
        try:
            value = extract_json(trace.final_output, p.extract)
        except json.JSONDecodeError:
            return CheckOutcome(False, "output is not valid JSON")
        errors = list(jsonschema.Draft202012Validator(p.schema_).iter_errors(value))
        if errors:
            err = min(errors, key=lambda e: list(e.path))
            where = "/".join(str(x) for x in err.path) or "<root>"
            return CheckOutcome(False, f"{where}: {err.message}")
        return CheckOutcome(True)


class ForbiddenContentParams(_Params):
    patterns: list[str] = Field(min_length=1)
    scope: Literal["output", "tool_args", "all"] = "output"
    case_sensitive: bool = False

    @field_validator("patterns")
    @classmethod
    def _check(cls, v: list[str]) -> list[str]:
        for pat in v:
            _compile(pat)
        return v


@register
class ForbiddenContentCheck(Check):
    type_name = "forbidden_content"
    Params = ForbiddenContentParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: ForbiddenContentParams = self.params  # type: ignore[assignment]
        texts: list[tuple[str, str]] = []
        if p.scope in ("output", "all"):
            texts.append(("output", trace.final_output))
        if p.scope in ("tool_args", "all"):
            for call in trace.tool_calls:
                texts.append((f"{call.name} args", json.dumps(call.arguments, sort_keys=True)))
        flags = 0 if p.case_sensitive else re.IGNORECASE
        for pat in p.patterns:
            rx = _compile(pat, flags)
            for where, text in texts:
                if rx.search(text):
                    return CheckOutcome(False, f"forbidden pattern /{pat}/ found in {where}")
        return CheckOutcome(True)


# --------------------------------------------------------------------------- budgets


class MaxStepsParams(_Params):
    max: int = Field(ge=0)
    count: Literal["tool_calls", "llm_calls", "steps"] = "tool_calls"


@register
class MaxStepsCheck(Check):
    type_name = "max_steps"
    Params = MaxStepsParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: MaxStepsParams = self.params  # type: ignore[assignment]
        n = {
            "tool_calls": len(trace.tool_calls),
            "llm_calls": len(trace.llm_calls),
            "steps": len(trace.steps),
        }[p.count]
        ok = n <= p.max
        return CheckOutcome(ok, "" if ok else f"{n} {p.count} > max {p.max}", score=float(n))


class TokenBudgetParams(_Params):
    max_total: int | None = Field(default=None, ge=0)
    max_input: int | None = Field(default=None, ge=0)
    max_output: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _any(self) -> TokenBudgetParams:
        if self.max_total is None and self.max_input is None and self.max_output is None:
            raise ValueError("set at least one of max_total, max_input, max_output")
        return self


@register
class TokenBudgetCheck(Check):
    type_name = "token_budget"
    Params = TokenBudgetParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: TokenBudgetParams = self.params  # type: ignore[assignment]
        u = trace.usage
        over = []
        for label, actual, limit in (
            ("total", u.total_tokens, p.max_total),
            ("input", u.input_tokens, p.max_input),
            ("output", u.output_tokens, p.max_output),
        ):
            if limit is not None and actual > limit:
                over.append(f"{label} tokens {actual} > {limit}")
        return CheckOutcome(not over, "; ".join(over), score=float(u.total_tokens))


class CostBudgetParams(_Params):
    max_usd: float = Field(ge=0)


@register
class CostBudgetCheck(Check):
    type_name = "cost_budget"
    Params = CostBudgetParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: CostBudgetParams = self.params  # type: ignore[assignment]
        cost = trace.usage.cost_usd
        if cost is None:
            return CheckOutcome(False, "cost unknown: trace has no cost and no pricing matched")
        ok = cost <= p.max_usd
        return CheckOutcome(ok, "" if ok else f"cost ${cost:.4f} > ${p.max_usd:.4f}", score=cost)


class LatencyBudgetParams(_Params):
    max_ms: float = Field(ge=0)


@register
class LatencyBudgetCheck(Check):
    type_name = "latency_budget"
    Params = LatencyBudgetParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: LatencyBudgetParams = self.params  # type: ignore[assignment]
        if trace.latency_ms is None:
            return CheckOutcome(False, "latency unknown")
        ok = trace.latency_ms <= p.max_ms
        msg = "" if ok else f"latency {trace.latency_ms:.0f} ms > {p.max_ms:.0f} ms"
        return CheckOutcome(ok, msg, score=trace.latency_ms)


class NoErrorParams(_Params):
    allow_tool_errors: bool = False


@register
class NoErrorCheck(Check):
    type_name = "no_error"
    Params = NoErrorParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: NoErrorParams = self.params  # type: ignore[assignment]
        if trace.error:
            return CheckOutcome(False, f"run error: {trace.error}")
        if not p.allow_tool_errors:
            for call in trace.tool_calls:
                if call.error:
                    return CheckOutcome(False, f"tool {call.name} error: {call.error}")
        return CheckOutcome(True)
