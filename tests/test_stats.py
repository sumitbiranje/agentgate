from __future__ import annotations

import math

import pytest

from agentgate.stats import (
    bootstrap_ci,
    fisher_exact,
    mean,
    pass_at_k,
    pass_hat_k,
    pct_change,
    percentile,
)


def test_pass_at_k_known_values() -> None:
    # n=5, c=2, k=3: 1 - C(3,3)/C(5,3) = 1 - 1/10
    assert pass_at_k(5, 2, 3) == pytest.approx(0.9)
    assert pass_at_k(5, 0, 3) == 0.0
    assert pass_at_k(5, 5, 3) == 1.0
    assert pass_at_k(5, 3, 3) == 1.0  # n - c < k
    assert pass_at_k(3, 1, 1) == pytest.approx(1 / 3)
    assert pass_at_k(0, 0, 3) == 0.0


def test_pass_hat_k_known_values() -> None:
    # n=5, c=4, k=3: C(4,3)/C(5,3) = 4/10
    assert pass_hat_k(5, 4, 3) == pytest.approx(0.4)
    assert pass_hat_k(5, 2, 3) == 0.0
    assert pass_hat_k(5, 5, 3) == 1.0
    assert pass_hat_k(2, 1, 5) == 0.0  # k clipped to n=2: C(1,2)/C(2,2)
    assert pass_hat_k(2, 2, 5) == 1.0
    assert pass_hat_k(0, 0, 1) == 0.0


def test_pass_hat_k_never_exceeds_pass_at_k() -> None:
    for n in range(1, 8):
        for c in range(n + 1):
            for k in range(1, 5):
                assert pass_hat_k(n, c, k) <= pass_at_k(n, c, k) + 1e-12


def test_fisher_exact_matches_reference_values() -> None:
    # Well-known two-sided reference values (e.g. 2/252 for a perfect 5x5 split, and the
    # 1,9,11,3 example from the SciPy documentation).
    assert fisher_exact(5, 0, 0, 5) == pytest.approx(0.007937, rel=1e-3)
    assert fisher_exact(3, 2, 5, 0) == pytest.approx(0.444444, rel=1e-3)
    assert fisher_exact(1, 9, 11, 3) == pytest.approx(0.002759, rel=1e-3)
    assert fisher_exact(5, 0, 5, 0) == 1.0
    assert fisher_exact(0, 0, 0, 0) == 1.0


def test_fisher_is_symmetric() -> None:
    assert fisher_exact(4, 1, 1, 4) == pytest.approx(fisher_exact(1, 4, 4, 1))


def test_percentile_linear_interpolation() -> None:
    xs = [1.0, 2.0, 3.0, 4.0]
    assert percentile(xs, 50) == pytest.approx(2.5)
    assert percentile(xs, 95) == pytest.approx(3.85)
    assert percentile(xs, 0) == 1.0
    assert percentile(xs, 100) == 4.0
    assert percentile([7.0], 95) == 7.0
    assert percentile([], 50) is None


def test_mean_and_pct_change() -> None:
    assert mean([1, 2, 3]) == 2
    assert mean([]) is None
    assert pct_change(10, 12) == pytest.approx(20.0)
    assert pct_change(None, 1) is None
    assert pct_change(0, 0) == 0.0
    assert math.isinf(pct_change(0, 1) or 0)


def test_bootstrap_is_seeded_and_brackets_the_estimate() -> None:
    values = {f"c{i}": float(i % 3 - 1) for i in range(30)}

    def stat(keys: list[str] | tuple[str, ...]) -> float:
        return sum(values[k] for k in keys) / len(keys)

    keys = sorted(values)
    a = bootstrap_ci(keys, stat, samples=500, seed=1)
    b = bootstrap_ci(keys, stat, samples=500, seed=1)
    c = bootstrap_ci(keys, stat, samples=500, seed=2)
    assert a == b
    assert a != c
    assert a[0] <= stat(keys) <= a[1]
    assert bootstrap_ci([], stat) == (0.0, 0.0)
