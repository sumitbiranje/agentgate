"""Small, exact statistics. No numpy: the maths is short and worth keeping visible."""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate of P(at least one of k sampled runs passes), n runs, c passes."""
    if n <= 0:
        return 0.0
    k = min(k, n)
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate of P(all k sampled runs pass) -- "pass^k", a reliability measure."""
    if n <= 0:
        return 0.0
    k = min(k, n)
    return math.comb(c, k) / math.comb(n, k)


def _hypergeom_pmf(a: int, row1: int, col1: int, total: int) -> float:
    return math.comb(col1, a) * math.comb(total - col1, row1 - a) / math.comb(total, row1)


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact test p-value for the 2x2 table [[a, b], [c, d]].

    Sums the probabilities of all tables with the same margins that are no more likely
    than the observed one (the standard "minimum likelihood" two-sided definition).
    """
    row1, col1, total = a + b, a + c, a + b + c + d
    if total == 0:
        return 1.0
    lo, hi = max(0, row1 + col1 - total), min(row1, col1)
    p_obs = _hypergeom_pmf(a, row1, col1, total)
    eps = 1e-12 * p_obs
    p = sum(
        pr for x in range(lo, hi + 1) if (pr := _hypergeom_pmf(x, row1, col1, total)) <= p_obs + eps
    )
    return min(1.0, p)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default method)."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def pct_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None:
        return None
    if old == 0:
        return 0.0 if new == 0 else math.inf
    return (new - old) / old * 100.0


def bootstrap_ci(
    keys: Sequence[str],
    statistic: Callable[[Sequence[str]], float],
    *,
    samples: int = 2000,
    seed: int = 0,
    level: float = 0.95,
) -> tuple[float, float]:
    """Percentile bootstrap CI, resampling *keys* (cases) with replacement.

    Resampling whole cases (a cluster bootstrap) respects that repeated runs of the same
    case are correlated. Seeded, so the same inputs always give the same interval.
    """
    if not keys:
        return (0.0, 0.0)
    rng = random.Random(seed)
    stats = sorted(statistic([rng.choice(keys) for _ in keys]) for _ in range(samples))
    alpha = (1 - level) / 2
    lo = percentile(stats, alpha * 100)
    hi = percentile(stats, (1 - alpha) * 100)
    if lo is None or hi is None:  # pragma: no cover - stats is never empty here
        return (0.0, 0.0)
    return (lo, hi)
