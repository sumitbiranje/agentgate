"""Gate rules: turn a comparison into pass/fail results and an exit code."""

from __future__ import annotations

import math

from agentgate.config import GateConfig
from agentgate.models import CaseComparison, Delta, GateResult, VariantSummary

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_USAGE = 2
EXIT_JUDGE = 3


def _pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    if math.isinf(x):
        return "+inf%"
    return f"{x:+.1f}%"


def _rate(x: float) -> str:
    return f"{x * 100:.1f}%"


def _increase_rule(
    rule: str, label: str, limit: float | None, actual: float | None
) -> GateResult | None:
    if limit is None:
        return None
    if actual is None:
        return GateResult(
            rule=rule,
            passed=True,
            threshold=f"<= +{limit:g}%",
            actual="n/a",
            message=f"{label} not available in both variants; rule not applied",
        )
    ok = actual <= limit
    return GateResult(
        rule=rule,
        passed=ok,
        threshold=f"<= +{limit:g}%",
        actual=_pct(actual),
        message=f"{label} changed by {_pct(actual)}"
        + ("" if ok else f", above the +{limit:g}% limit"),
    )


def evaluate_gates(
    gates: GateConfig,
    cases: list[CaseComparison],
    candidate: VariantSummary,
    baseline: VariantSummary | None = None,
    delta: Delta | None = None,
) -> list[GateResult]:
    """Evaluate every configured rule. Relative rules need a baseline and are skipped
    in single-variant (``check``) mode."""
    results: list[GateResult] = []
    compare = baseline is not None and delta is not None

    if gates.min_pass_rate is not None:
        ok = candidate.pass_rate >= gates.min_pass_rate
        results.append(
            GateResult(
                rule="min_pass_rate",
                passed=ok,
                threshold=f">= {_rate(gates.min_pass_rate)}",
                actual=_rate(candidate.pass_rate),
                message=f"candidate pass rate {_rate(candidate.pass_rate)}",
            )
        )

    if gates.no_new_critical_failures:
        if compare:
            bad = [
                c.case_id
                for c in cases
                if c.critical
                and c.candidate is not None
                and (
                    c.label == "regressed"
                    or (
                        c.baseline is not None
                        and c.baseline.pass_rate == 1.0
                        and c.candidate.pass_rate < 1.0
                    )
                )
            ]
            msg = (
                "no critical case lost a passing run"
                if not bad
                else ("critical case(s) now failing: " + ", ".join(bad))
            )
        else:
            bad = [c.case_id for c in cases if c.critical and c.label != "passing"]
            msg = (
                "all critical cases pass every run"
                if not bad
                else ("critical case(s) not passing every run: " + ", ".join(bad))
            )
        results.append(
            GateResult(
                rule="no_new_critical_failures",
                passed=not bad,
                threshold="0",
                actual=str(len(bad)),
                message=msg,
            )
        )

    if gates.fail_on_flaky:
        flaky = [c.case_id for c in cases if c.label == "flaky"]
        results.append(
            GateResult(
                rule="fail_on_flaky",
                passed=not flaky,
                threshold="0",
                actual=str(len(flaky)),
                message="no flaky cases" if not flaky else "flaky: " + ", ".join(flaky),
            )
        )

    if not compare:
        return results
    assert baseline is not None
    assert delta is not None

    if gates.max_regressed_cases is not None:
        reg = [c.case_id for c in cases if c.label == "regressed"]
        ok = len(reg) <= gates.max_regressed_cases
        results.append(
            GateResult(
                rule="max_regressed_cases",
                passed=ok,
                threshold=f"<= {gates.max_regressed_cases}",
                actual=str(len(reg)),
                message="no regressed cases" if not reg else "regressed: " + ", ".join(reg),
            )
        )

    if gates.max_pass_rate_drop is not None:
        drop = -delta.pass_rate
        ok = drop <= gates.max_pass_rate_drop + 1e-12
        ci = ""
        if delta.pass_rate_ci is not None:
            lo, hi = delta.pass_rate_ci.low * 100, delta.pass_rate_ci.high * 100
            ci = f" (95% CI {lo:+.1f} to {hi:+.1f} pts)"
        results.append(
            GateResult(
                rule="max_pass_rate_drop",
                passed=ok,
                threshold=f"<= {gates.max_pass_rate_drop * 100:.1f} pts",
                actual=f"{delta.pass_rate * 100:+.1f} pts",
                message=(
                    f"pass rate {_rate(baseline.pass_rate)} -> {_rate(candidate.pass_rate)}{ci}"
                ),
            )
        )

    if gates.fail_on_removed:
        removed = [c.case_id for c in cases if c.label == "removed"]
        results.append(
            GateResult(
                rule="fail_on_removed",
                passed=not removed,
                threshold="0",
                actual=str(len(removed)),
                message="every baseline case has candidate runs"
                if not removed
                else "missing candidate runs: " + ", ".join(removed),
            )
        )

    for r in (
        _increase_rule(
            "max_cost_increase_pct",
            "mean cost per run",
            gates.max_cost_increase_pct,
            delta.cost_pct,
        ),
        _increase_rule(
            "max_p95_latency_increase_pct",
            "p95 latency",
            gates.max_p95_latency_increase_pct,
            delta.p95_latency_pct,
        ),
        _increase_rule(
            "max_token_increase_pct",
            "mean tokens per run",
            gates.max_token_increase_pct,
            delta.tokens_pct,
        ),
    ):
        if r is not None:
            results.append(r)
    return results


def exit_code(results: list[GateResult]) -> int:
    return EXIT_OK if all(r.passed for r in results) else EXIT_GATE_FAILED
