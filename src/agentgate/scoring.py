"""Score traces against the suite: every applicable check on every run."""

from __future__ import annotations

from collections import defaultdict

from agentgate.checks import Check, CheckContext, build_check
from agentgate.config import CaseSpec, SuiteConfig
from agentgate.judge.base import Judge
from agentgate.models import RunVerdict, Trace


def apply_pricing(trace: Trace, config: SuiteConfig) -> Trace:
    """Fill ``usage.cost_usd`` from per-model pricing when the trace has no cost.

    Cost is summed per LLM step so mixed-model runs are priced correctly. If any step's
    model has no price, cost stays unknown rather than being silently under-counted.
    """
    if trace.usage.cost_usd is not None or not config.pricing:
        return trace
    total = 0.0
    llm_steps = trace.llm_calls
    if not llm_steps:
        return trace
    for step in llm_steps:
        price = config.pricing.get(step.name)
        if price is None:
            return trace
        total += (step.input_tokens * price.input + step.output_tokens * price.output) / 1e6
    usage = trace.usage.model_copy(update={"cost_usd": round(total, 8)})
    return trace.model_copy(update={"usage": usage})


def _check_ids(checks: list[Check]) -> list[str]:
    ids: list[str] = []
    counts: dict[str, int] = defaultdict(int)
    for c in checks:
        base = c.spec.id or c.type_name
        counts[base] += 1
        ids.append(base if counts[base] == 1 else f"{base}#{counts[base]}")
    return ids


class Scorer:
    """Builds each case's checks once, then scores any number of traces."""

    def __init__(self, config: SuiteConfig, judge: Judge | None = None) -> None:
        self.config = config
        self.ctx = CheckContext(config=config, judge=judge)
        self._checks: dict[str, tuple[CaseSpec, list[Check], list[str]]] = {}
        for case in config.cases:
            checks = [build_check(spec, config) for spec in config.checks_for(case)]
            self._checks[case.id] = (case, checks, _check_ids(checks))

    def score(self, trace: Trace) -> RunVerdict:
        case, checks, ids = self._checks[trace.case_id]
        trace = apply_pricing(trace, self.config)
        results = [c.run(trace, case, self.ctx, cid) for c, cid in zip(checks, ids, strict=True)]
        passed = all(r.passed or r.skipped or not r.required for r in results)
        return RunVerdict(
            case_id=trace.case_id,
            run_id=trace.run_id,
            attempt=trace.attempt,
            passed=passed,
            checks=results,
            cost_usd=trace.usage.cost_usd,
            latency_ms=trace.latency_ms,
            total_tokens=trace.usage.total_tokens,
            steps=len(trace.steps),
            tool_calls=trace.tool_names,
            error=trace.error,
        )

    def score_all(self, traces: list[Trace]) -> tuple[dict[str, list[RunVerdict]], list[str]]:
        """Group verdicts by case id. Traces for unknown cases are reported, not scored."""
        by_case: dict[str, list[RunVerdict]] = {c.id: [] for c in self.config.cases}
        unknown: dict[str, int] = defaultdict(int)
        for t in traces:
            if t.case_id not in self._checks:
                unknown[t.case_id] += 1
                continue
            by_case[t.case_id].append(self.score(t))
        warnings = [
            f"{n} trace(s) for unknown case {cid!r} ignored" for cid, n in sorted(unknown.items())
        ]
        for verdicts in by_case.values():
            verdicts.sort(key=lambda v: (v.attempt, v.run_id))
        return by_case, warnings
