from __future__ import annotations

import pytest

from agentgate.compare import case_stats, compare_variants, label_pair, label_single, single_variant
from agentgate.models import CaseStats, CheckResult, RunVerdict

from .conftest import make_config


def stats(passes: int, n: int) -> CaseStats:
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


@pytest.mark.parametrize(
    ("base", "cand", "label"),
    [
        ((5, 5), (5, 5), "unchanged"),
        ((0, 5), (0, 5), "unchanged"),
        ((5, 5), (0, 5), "regressed"),
        ((1, 1), (0, 1), "regressed"),  # single run: deterministic verdict
        ((0, 3), (3, 3), "improved"),
        ((10, 10), (2, 10), "regressed"),  # mixed, significant
        ((1, 10), (9, 10), "improved"),
        ((5, 5), (4, 5), "flaky"),  # mixed, not significant
        ((3, 5), (3, 5), "flaky"),
    ],
)
def test_label_pair(base: tuple[int, int], cand: tuple[int, int], label: str) -> None:
    got, reason, p = label_pair(stats(*base), stats(*cand), alpha=0.1)
    assert got == label
    assert f"{base[0]}/{base[1]}" in reason
    if label == "flaky":
        assert p is not None
        assert p > 0.1


def test_label_new_removed() -> None:
    assert label_pair(None, stats(1, 1), 0.1)[0] == "new"
    assert label_pair(stats(1, 1), None, 0.1)[0] == "removed"
    assert label_pair(None, None, 0.1)[0] == "unchanged"


def test_label_single() -> None:
    assert label_single(stats(3, 3))[0] == "passing"
    assert label_single(stats(0, 3))[0] == "failing"
    assert label_single(stats(1, 3))[0] == "flaky"
    assert label_single(None)[0] == "failing"


def verdict(
    case_id: str,
    passed: bool,
    attempt: int = 0,
    cost: float | None = 0.01,
    latency: float = 1000.0,
    tokens: int = 100,
) -> RunVerdict:
    checks = [] if passed else [CheckResult(check_id="chk", type="t", passed=False, message="boom")]
    return RunVerdict(
        case_id=case_id,
        run_id=f"{case_id}{attempt}",
        attempt=attempt,
        passed=passed,
        checks=checks,
        cost_usd=cost,
        latency_ms=latency,
        total_tokens=tokens,
        steps=1,
        tool_calls=[],
    )


def test_case_stats_aggregates() -> None:
    s = case_stats([verdict("a", True), verdict("a", False, 1, cost=0.03)], k=2)
    assert s is not None
    assert (s.n, s.passes, s.pass_rate) == (2, 1, 0.5)
    assert s.pass_at_k == 1.0
    assert s.pass_hat_k == 0.0
    assert s.mean_cost_usd == pytest.approx(0.02)
    assert s.check_failures == {"chk": 1}
    assert s.sample_failure == "chk: boom"
    assert case_stats([], 3) is None
    unknown = case_stats([verdict("a", True, cost=None), verdict("a", True, 1)], 2)
    assert unknown is not None
    assert unknown.mean_cost_usd is None  # partial cost data is not averaged


def test_compare_variants_end_to_end() -> None:
    config = make_config(
        [{"id": "a"}, {"id": "b", "critical": True}, {"id": "gone"}, {"id": "fresh"}]
    )
    base = {
        "a": [verdict("a", True, i) for i in range(3)],
        "b": [verdict("b", True, i) for i in range(3)],
        "gone": [verdict("gone", True)],
    }
    cand = {
        "a": [verdict("a", True, i, cost=0.02, latency=2000, tokens=200) for i in range(3)],
        "b": [verdict("b", False, i, cost=0.02, latency=2000, tokens=200) for i in range(3)],
        "fresh": [verdict("fresh", True, cost=0.02, latency=2000, tokens=200)],
    }
    b, c, d, cases = compare_variants(config, base, cand)
    labels = {x.case_id: x.label for x in cases}
    assert labels == {"a": "unchanged", "b": "regressed", "gone": "removed", "fresh": "new"}
    assert cases[0].case_id == "b"  # most severe first
    assert b.pass_rate == 1.0
    assert c.pass_rate == pytest.approx(2 / 3)
    assert d.cost_pct == pytest.approx(100.0)
    assert d.tokens_pct == pytest.approx(100.0)
    assert d.p95_latency_pct == pytest.approx(100.0)
    assert d.pass_rate_ci is not None
    assert d.pass_rate_ci.low <= -0.5 <= d.pass_rate_ci.high


def test_single_variant() -> None:
    config = make_config([{"id": "a"}, {"id": "b"}])
    summary, cases = single_variant(config, {"a": [verdict("a", True)], "b": [verdict("b", False)]})
    assert summary.pass_rate == 0.5
    assert {c.case_id: c.label for c in cases} == {"a": "passing", "b": "failing"}
