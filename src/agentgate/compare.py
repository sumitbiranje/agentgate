"""Per-case statistics, case labels and suite-level summaries.

Labelling rule (spec section 7):
1. Case only on one side -> ``new`` / ``removed``.
2. Both sides unanimous (all pass or all fail): the software-style deterministic
   verdict. Pass->fail is ``regressed``, fail->pass ``improved``, else ``unchanged``.
3. Mixed outcomes: two-sided Fisher exact test. ``p <= alpha`` -> regressed/improved
   by direction; otherwise ``flaky`` -- the case is non-deterministic and the data
   cannot tell the versions apart. Inconclusive is never silently reported as fine.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from agentgate.config import SuiteConfig
from agentgate.models import (
    CaseComparison,
    CaseStats,
    Delta,
    Interval,
    Label,
    RunVerdict,
    VariantSummary,
)
from agentgate.stats import (
    bootstrap_ci,
    fisher_exact,
    mean,
    pass_at_k,
    pass_hat_k,
    pct_change,
    percentile,
)


def case_stats(verdicts: Sequence[RunVerdict], k: int) -> CaseStats | None:
    if not verdicts:
        return None
    n = len(verdicts)
    c = sum(v.passed for v in verdicts)
    failures: Counter[str] = Counter()
    sample: str | None = None
    for v in verdicts:
        for chk in v.failed_checks:
            failures[chk.check_id] += 1
            if sample is None:
                sample = f"{chk.check_id}: {chk.message}" if chk.message else chk.check_id
    costs = [v.cost_usd for v in verdicts if v.cost_usd is not None]
    lats = [v.latency_ms for v in verdicts if v.latency_ms is not None]
    return CaseStats(
        n=n,
        passes=c,
        pass_rate=c / n,
        pass_at_k=pass_at_k(n, c, k),
        pass_hat_k=pass_hat_k(n, c, k),
        mean_cost_usd=mean(costs) if len(costs) == n else None,
        mean_latency_ms=mean(lats),
        mean_tokens=sum(v.total_tokens for v in verdicts) / n,
        check_failures=dict(sorted(failures.items())),
        sample_failure=sample,
    )


def _fmt_rate(s: CaseStats) -> str:
    return f"{s.passes}/{s.n}"


def label_pair(
    base: CaseStats | None, cand: CaseStats | None, alpha: float
) -> tuple[Label, str, float | None]:
    if base is None and cand is None:
        return "unchanged", "no runs in either variant", None
    if base is None:
        assert cand is not None
        return "new", f"only in candidate ({_fmt_rate(cand)} passed)", None
    if cand is None:
        return "removed", f"no candidate runs (baseline {_fmt_rate(base)} passed)", None
    rates = f"baseline {_fmt_rate(base)} -> candidate {_fmt_rate(cand)}"
    unanimous = base.pass_rate in (0.0, 1.0) and cand.pass_rate in (0.0, 1.0)
    if unanimous:
        if base.pass_rate == cand.pass_rate:
            state = "passing" if cand.pass_rate == 1.0 else "failing"
            return "unchanged", f"{rates} (consistently {state})", None
        if base.pass_rate > cand.pass_rate:
            return "regressed", f"{rates}: every run used to pass, now every run fails", None
        return "improved", f"{rates}: every run used to fail, now every run passes", None
    p = fisher_exact(base.passes, base.n - base.passes, cand.passes, cand.n - cand.passes)
    if p <= alpha:
        direction: Label = "regressed" if cand.pass_rate < base.pass_rate else "improved"
        return direction, f"{rates} (Fisher p={p:.3f} <= {alpha})", p
    if base.pass_rate == 1.0:
        return (
            "flaky",
            f"{rates}: new failing run(s), not significant at this run count (p={p:.3f})",
            p,
        )
    return "flaky", f"{rates}: mixed outcomes, difference not significant (p={p:.3f})", p


def label_single(stats: CaseStats | None) -> tuple[Label, str]:
    if stats is None:
        return "failing", "no runs recorded"
    if stats.pass_rate == 1.0:
        return "passing", f"{_fmt_rate(stats)} runs passed"
    if stats.pass_rate == 0.0:
        return "failing", f"{_fmt_rate(stats)} runs passed"
    return "flaky", f"{_fmt_rate(stats)} runs passed (non-deterministic)"


def summarise(
    name: str, by_case: dict[str, list[RunVerdict]], stats: dict[str, CaseStats | None]
) -> VariantSummary:
    present = {cid: s for cid, s in stats.items() if s is not None}
    runs = [v for vs in by_case.values() for v in vs]
    costs = [v.cost_usd for v in runs if v.cost_usd is not None]
    lats = [v.latency_ms for v in runs if v.latency_ms is not None]
    n_cases = len(present)
    return VariantSummary(
        name=name,
        cases=n_cases,
        runs=len(runs),
        pass_rate=sum(s.pass_rate for s in present.values()) / n_cases if n_cases else 0.0,
        pass_at_k=sum(s.pass_at_k for s in present.values()) / n_cases if n_cases else 0.0,
        pass_hat_k=sum(s.pass_hat_k for s in present.values()) / n_cases if n_cases else 0.0,
        mean_cost_usd=mean(costs) if runs and len(costs) == len(runs) else None,
        mean_tokens=sum(v.total_tokens for v in runs) / len(runs) if runs else 0.0,
        p50_latency_ms=percentile(lats, 50),
        p95_latency_ms=percentile(lats, 95),
    )


SEVERITY = {
    "regressed": 0,
    "removed": 1,
    "flaky": 2,
    "failing": 3,
    "new": 4,
    "improved": 5,
    "unchanged": 6,
    "passing": 7,
}


def sort_cases(cases: list[CaseComparison]) -> list[CaseComparison]:
    return sorted(cases, key=lambda c: (SEVERITY[c.label], not c.critical, c.case_id))


def compare_variants(
    config: SuiteConfig,
    base_runs: dict[str, list[RunVerdict]],
    cand_runs: dict[str, list[RunVerdict]],
) -> tuple[VariantSummary, VariantSummary, Delta, list[CaseComparison]]:
    k = config.k
    base_stats = {cid: case_stats(v, k) for cid, v in base_runs.items()}
    cand_stats = {cid: case_stats(v, k) for cid, v in cand_runs.items()}
    cases = []
    for case in config.cases:
        b, c = base_stats.get(case.id), cand_stats.get(case.id)
        if b is None and c is None:
            continue
        label, reason, p = label_pair(b, c, config.alpha)
        cases.append(
            CaseComparison(
                case_id=case.id,
                description=case.description,
                critical=case.critical,
                label=label,
                reason=reason,
                p_value=p,
                baseline=b,
                candidate=c,
            )
        )
    base_sum = summarise("baseline", base_runs, base_stats)
    cand_sum = summarise("candidate", cand_runs, cand_stats)

    paired = sorted(
        cid for cid, s in base_stats.items() if s is not None and cand_stats.get(cid) is not None
    )

    b_rates = {cid: s.pass_rate for cid, s in base_stats.items() if s is not None}
    c_rates = {cid: s.pass_rate for cid, s in cand_stats.items() if s is not None}

    def diff(sample: Sequence[str]) -> float:
        return sum(c_rates[c] - b_rates[c] for c in sample) / len(sample)

    ci = None
    if paired:
        lo, hi = bootstrap_ci(paired, diff, samples=config.bootstrap_samples, seed=config.seed)
        ci = Interval(low=lo, high=hi)
    delta = Delta(
        pass_rate=cand_sum.pass_rate - base_sum.pass_rate,
        pass_rate_ci=ci,
        cost_pct=pct_change(base_sum.mean_cost_usd, cand_sum.mean_cost_usd),
        tokens_pct=pct_change(base_sum.mean_tokens, cand_sum.mean_tokens),
        p95_latency_pct=pct_change(base_sum.p95_latency_ms, cand_sum.p95_latency_ms),
    )
    return base_sum, cand_sum, delta, sort_cases(cases)


def single_variant(
    config: SuiteConfig, runs: dict[str, list[RunVerdict]]
) -> tuple[VariantSummary, list[CaseComparison]]:
    stats = {cid: case_stats(v, config.k) for cid, v in runs.items()}
    cases = []
    for case in config.cases:
        s = stats.get(case.id)
        label, reason = label_single(s)
        cases.append(
            CaseComparison(
                case_id=case.id,
                description=case.description,
                critical=case.critical,
                label=label,
                reason=reason,
                candidate=s,
            )
        )
    return summarise("candidate", runs, stats), sort_cases(cases)
