"""Core data model.

Everything that flows between loaders, checks, statistics, gates and reports is one of
these Pydantic models. Keeping them in one module makes the pipeline easy to follow:
``Trace`` -> ``RunVerdict`` -> ``CaseStats`` / ``CaseComparison`` -> ``Report``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TRACE_SCHEMA = "agentgate.trace/v1"
REPORT_SCHEMA = "agentgate.report/v1"


class StepType(StrEnum):
    LLM = "llm"
    TOOL_CALL = "tool_call"


class Step(BaseModel):
    """One thing the agent did: a model call or a tool call."""

    model_config = ConfigDict(extra="allow")

    type: StepType
    name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Trace(BaseModel):
    """One recorded agent run for one case."""

    model_config = ConfigDict(extra="ignore")

    case_id: str
    run_id: str = ""
    attempt: int = 0
    variant: str = ""
    input: Any = None
    steps: list[Step] = Field(default_factory=list)
    final_output: str = ""
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def tool_calls(self) -> list[Step]:
        return [s for s in self.steps if s.type is StepType.TOOL_CALL]

    @property
    def llm_calls(self) -> list[Step]:
        return [s for s in self.steps if s.type is StepType.LLM]

    @property
    def tool_names(self) -> list[str]:
        return [s.name for s in self.tool_calls]


class CheckResult(BaseModel):
    check_id: str
    type: str
    passed: bool
    required: bool = True
    skipped: bool = False
    message: str = ""
    score: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RunVerdict(BaseModel):
    """The outcome of scoring one trace against its case."""

    case_id: str
    run_id: str
    attempt: int
    passed: bool
    checks: list[CheckResult]
    cost_usd: float | None
    latency_ms: float | None
    total_tokens: int
    steps: int
    tool_calls: list[str]
    error: str | None = None

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.required and not c.passed and not c.skipped]


class CaseStats(BaseModel):
    """Aggregate of all runs of one case in one variant."""

    n: int
    passes: int
    pass_rate: float
    pass_at_k: float
    pass_hat_k: float
    mean_cost_usd: float | None
    mean_latency_ms: float | None
    mean_tokens: float
    check_failures: dict[str, int] = Field(default_factory=dict)
    sample_failure: str | None = None


# compare mode uses the first six labels; check mode (no baseline) uses passing/failing/flaky.
Label = Literal[
    "regressed", "improved", "unchanged", "flaky", "new", "removed", "passing", "failing"
]


class CaseComparison(BaseModel):
    case_id: str
    description: str = ""
    critical: bool = False
    label: Label
    reason: str
    p_value: float | None = None
    baseline: CaseStats | None = None
    candidate: CaseStats | None = None


class Interval(BaseModel):
    low: float
    high: float


class VariantSummary(BaseModel):
    """Suite-level numbers for one variant."""

    name: str
    cases: int
    runs: int
    pass_rate: float
    pass_at_k: float
    pass_hat_k: float
    mean_cost_usd: float | None
    mean_tokens: float
    p50_latency_ms: float | None
    p95_latency_ms: float | None


class Delta(BaseModel):
    pass_rate: float
    pass_rate_ci: Interval | None = None
    cost_pct: float | None = None
    tokens_pct: float | None = None
    p95_latency_pct: float | None = None


class GateResult(BaseModel):
    rule: str
    passed: bool
    threshold: str
    actual: str
    message: str


class Report(BaseModel):
    """Everything a report renderer needs. Serialised as the JSON report."""

    schema_: str = Field(default=REPORT_SCHEMA, alias="schema")
    tool_version: str
    suite: str
    mode: Literal["check", "compare"]
    k: int
    alpha: float
    seed: int
    generated_at: str
    baseline: VariantSummary | None = None
    candidate: VariantSummary
    delta: Delta | None = None
    cases: list[CaseComparison]
    gates: list[GateResult]
    passed: bool
    exit_code: int
    warnings: list[str] = Field(default_factory=list)
    judge: str | None = None

    model_config = ConfigDict(populate_by_name=True)

    @property
    def label_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for case in self.cases:
            counts[case.label] = counts.get(case.label, 0) + 1
        return counts
