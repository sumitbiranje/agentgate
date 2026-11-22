from __future__ import annotations

import pytest

from agentgate.config import GateConfig
from agentgate.gates import EXIT_GATE_FAILED, EXIT_OK, evaluate_gates, exit_code
from agentgate.models import CaseComparison, CaseStats, Delta, Interval, VariantSummary


def summary(rate: float = 1.0) -> VariantSummary:
    return VariantSummary(
        name="x",
        cases=1,
        runs=5,
        pass_rate=rate,
        pass_at_k=rate,
        pass_hat_k=rate,
        mean_cost_usd=0.01,
        mean_tokens=100,
        p50_latency_ms=1000,
        p95_latency_ms=2000,
    )


def st(passes: int, n: int = 5) -> CaseStats:
    return CaseStats(
        n=n,
        passes=passes,
        pass_rate=passes / n,
        pass_at_k=0,
        pass_hat_k=0,
        mean_cost_usd=None,
        mean_latency_ms=None,
        mean_tokens=0,
    )


def cc(
    label: str, critical: bool = False, base: int | None = 5, cand: int | None = 5
) -> CaseComparison:
    return CaseComparison(
        case_id=f"{label}-{critical}",
        critical=critical,
        label=label,  # type: ignore[arg-type]
        reason="",
        baseline=st(base) if base is not None else None,
        candidate=st(cand) if cand is not None else None,
    )


def gate(
    rule: str,
    cases: list[CaseComparison],
    delta: Delta | None = None,
    cand_rate: float = 1.0,
    compare: bool = True,
    **cfg: object,
) -> bool:
    results = evaluate_gates(
        GateConfig.model_validate(cfg),
        cases,
        summary(cand_rate),
        summary() if compare else None,
        (delta or Delta(pass_rate=0.0)) if compare else None,
    )
    (r,) = [r for r in results if r.rule == rule]
    return r.passed


def test_no_gates_passes() -> None:
    assert evaluate_gates(GateConfig(), [], summary(), summary(), Delta(pass_rate=0)) == []
    assert exit_code([]) == EXIT_OK


def test_critical_failures() -> None:
    rule = "no_new_critical_failures"
    assert gate(rule, [cc("unchanged", True)], no_new_critical_failures=True)
    assert not gate(rule, [cc("flaky", True, 5, 4)], no_new_critical_failures=True)
    assert not gate(rule, [cc("regressed", True, 5, 0)], no_new_critical_failures=True)
    # failing before and after is not a *new* failure
    assert gate(rule, [cc("flaky", True, 3, 2)], no_new_critical_failures=True)
    # non-critical cases are ignored by this rule
    assert gate(rule, [cc("regressed", False, 5, 0)], no_new_critical_failures=True)


def test_critical_in_check_mode() -> None:
    rule = "no_new_critical_failures"
    assert gate(rule, [cc("passing", True)], compare=False, no_new_critical_failures=True)
    assert not gate(rule, [cc("flaky", True)], compare=False, no_new_critical_failures=True)


def test_max_regressed_cases() -> None:
    cases = [cc("regressed"), cc("unchanged")]
    assert not gate("max_regressed_cases", cases, max_regressed_cases=0)
    assert gate("max_regressed_cases", cases, max_regressed_cases=1)


def test_pass_rate_rules() -> None:
    d = Delta(pass_rate=-0.06, pass_rate_ci=Interval(low=-0.1, high=-0.02))
    assert not gate("max_pass_rate_drop", [], d, max_pass_rate_drop=0.05)
    assert gate("max_pass_rate_drop", [], Delta(pass_rate=-0.05), max_pass_rate_drop=0.05)
    assert gate("min_pass_rate", [], cand_rate=0.8, min_pass_rate=0.8)
    assert not gate("min_pass_rate", [], cand_rate=0.79, min_pass_rate=0.8)


@pytest.mark.parametrize(
    ("rule", "field", "cfg"),
    [
        ("max_cost_increase_pct", "cost_pct", "max_cost_increase_pct"),
        ("max_p95_latency_increase_pct", "p95_latency_pct", "max_p95_latency_increase_pct"),
        ("max_token_increase_pct", "tokens_pct", "max_token_increase_pct"),
    ],
)
def test_increase_rules(rule: str, field: str, cfg: str) -> None:
    assert gate(rule, [], Delta(pass_rate=0, **{field: 19.9}), **{cfg: 20})
    assert not gate(rule, [], Delta(pass_rate=0, **{field: 20.1}), **{cfg: 20})
    assert gate(rule, [], Delta(pass_rate=0, **{field: None}), **{cfg: 20})  # n/a -> not applied
    assert not gate(rule, [], Delta(pass_rate=0, **{field: float("inf")}), **{cfg: 20})


def test_flaky_and_removed() -> None:
    assert not gate("fail_on_flaky", [cc("flaky")], fail_on_flaky=True)
    assert gate("fail_on_flaky", [cc("unchanged")], fail_on_flaky=True)
    assert not gate("fail_on_removed", [cc("removed", cand=None)], fail_on_removed=True)
    assert gate("fail_on_removed", [cc("new", base=None)], fail_on_removed=True)


def test_relative_rules_skipped_without_baseline() -> None:
    results = evaluate_gates(
        GateConfig(max_cost_increase_pct=1, max_regressed_cases=0), [], summary()
    )
    assert results == []


def test_exit_code_on_failure() -> None:
    results = evaluate_gates(GateConfig(min_pass_rate=0.9), [], summary(0.5))
    assert exit_code(results) == EXIT_GATE_FAILED
    assert "50.0%" in results[0].actual
