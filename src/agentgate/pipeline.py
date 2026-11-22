"""End-to-end orchestration: load -> redact -> score -> compare -> gate -> Report.

The CLI is a thin wrapper around these two functions, which keeps them usable as a
library (e.g. from a pytest test in the user's own repo).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentgate import __version__
from agentgate.compare import compare_variants, single_variant
from agentgate.config import SuiteConfig
from agentgate.gates import evaluate_gates, exit_code
from agentgate.judge.cache import CachedJudge
from agentgate.judge.providers import build_judge
from agentgate.models import Report, RunVerdict, Trace
from agentgate.redact import Redactor
from agentgate.scoring import Scorer
from agentgate.traces.loader import Format, load_traces


def _uses_judge(config: SuiteConfig) -> bool:
    return any(s.type == "llm_judge" for c in config.cases for s in config.checks_for(c))


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Session:
    """Holds the per-invocation scorer, judge and redactor."""

    def __init__(self, config: SuiteConfig, judge_provider: str | None = None) -> None:
        self.config = config
        self.judge: CachedJudge | None = (
            build_judge(config, judge_provider) if _uses_judge(config) else None
        )
        self.redactor = Redactor(
            config.redact.patterns, config.redact.keys, use_builtins=config.redact.builtins
        )
        self.scorer = Scorer(config, self.judge)
        self.warnings: list[str] = []

    def load(
        self, paths: Sequence[str | Path], variant: str, fmt: Format, lenient: bool
    ) -> list[Trace]:
        result = load_traces(paths, fmt, variant=variant, lenient=lenient)
        self.warnings += [f"{variant}: {w}" for w in result.warnings]
        return [self.redactor.trace(t) for t in result.traces]

    def score(self, traces: list[Trace], variant: str) -> dict[str, list[RunVerdict]]:
        by_case, warnings = self.scorer.score_all(traces)
        self.warnings += [f"{variant}: {w}" for w in warnings]
        for cid, runs in by_case.items():
            if not runs:
                self.warnings.append(f"{variant}: no runs for case {cid!r}")
        return {cid: runs for cid, runs in by_case.items() if runs}

    @property
    def judge_label(self) -> str | None:
        if self.judge is None:
            return None
        return f"{self.judge.judge_id} (cache hits {self.judge.hits}, misses {self.judge.misses})"


def run_check(
    config: SuiteConfig,
    traces: Sequence[str | Path],
    *,
    fmt: Format = "auto",
    lenient: bool = False,
    judge_provider: str | None = None,
    generated_at: str | None = None,
) -> Report:
    s = Session(config, judge_provider)
    runs = s.score(s.load(traces, "candidate", fmt, lenient), "candidate")
    summary, cases = single_variant(config, runs)
    gates = evaluate_gates(config.gates, cases, summary)
    return Report(
        tool_version=__version__,
        suite=config.suite,
        mode="check",
        k=config.k,
        alpha=config.alpha,
        seed=config.seed,
        generated_at=generated_at or _now(),
        candidate=summary,
        cases=cases,
        gates=gates,
        passed=exit_code(gates) == 0,
        exit_code=exit_code(gates),
        warnings=s.warnings,
        judge=s.judge_label,
    )


def run_compare(
    config: SuiteConfig,
    baseline: Sequence[str | Path],
    candidate: Sequence[str | Path],
    *,
    fmt: Format = "auto",
    lenient: bool = False,
    judge_provider: str | None = None,
    generated_at: str | None = None,
) -> Report:
    s = Session(config, judge_provider)
    base_runs = s.score(s.load(baseline, "baseline", fmt, lenient), "baseline")
    cand_runs = s.score(s.load(candidate, "candidate", fmt, lenient), "candidate")
    base_sum, cand_sum, delta, cases = compare_variants(config, base_runs, cand_runs)
    gates = evaluate_gates(config.gates, cases, cand_sum, base_sum, delta)
    code = exit_code(gates)
    return Report(
        tool_version=__version__,
        suite=config.suite,
        mode="compare",
        k=config.k,
        alpha=config.alpha,
        seed=config.seed,
        generated_at=generated_at or _now(),
        baseline=base_sum,
        candidate=cand_sum,
        delta=delta,
        cases=cases,
        gates=gates,
        passed=code == 0,
        exit_code=code,
        warnings=s.warnings,
        judge=s.judge_label,
    )
