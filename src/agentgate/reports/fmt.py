"""Formatting helpers shared by every report renderer."""

from __future__ import annotations

import math

from agentgate.models import CaseStats, Report

LABEL_TEXT = {
    "regressed": "REGRESSED",
    "improved": "improved",
    "unchanged": "unchanged",
    "flaky": "FLAKY",
    "new": "new",
    "removed": "REMOVED",
    "passing": "passing",
    "failing": "FAILING",
}

ATTENTION = {"regressed", "removed", "flaky", "failing", "new", "improved"}


def rate(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.4f}"


def ms(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x / 1000:.2f} s" if x >= 1000 else f"{x:.0f} ms"


def pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    if math.isinf(x):
        return "+inf%"
    return f"{x:+.1f}%"


def pts(x: float) -> str:
    return f"{x * 100:+.1f} pts"


def runs(s: CaseStats | None) -> str:
    return "-" if s is None else f"{s.passes}/{s.n}"


def tokens(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.0f}"


def metric_rows(report: Report) -> list[tuple[str, str, str, str]]:
    """(metric, baseline, candidate, change) rows for the summary table."""
    c, b, d = report.candidate, report.baseline, report.delta
    if b is None or d is None:
        return [
            ("Pass rate", "", rate(c.pass_rate), ""),
            (f"pass@{report.k}", "", rate(c.pass_at_k), ""),
            (f"pass^{report.k}", "", rate(c.pass_hat_k), ""),
            ("Mean cost / run", "", usd(c.mean_cost_usd), ""),
            ("Mean tokens / run", "", tokens(c.mean_tokens), ""),
            ("p50 latency", "", ms(c.p50_latency_ms), ""),
            ("p95 latency", "", ms(c.p95_latency_ms), ""),
            ("Runs (cases)", "", f"{c.runs} ({c.cases})", ""),
        ]
    change = pts(d.pass_rate)
    if d.pass_rate_ci is not None:
        change += f" (95% CI {d.pass_rate_ci.low * 100:+.1f} to {d.pass_rate_ci.high * 100:+.1f})"
    return [
        ("Pass rate", rate(b.pass_rate), rate(c.pass_rate), change),
        (f"pass@{report.k}", rate(b.pass_at_k), rate(c.pass_at_k), pts(c.pass_at_k - b.pass_at_k)),
        (
            f"pass^{report.k}",
            rate(b.pass_hat_k),
            rate(c.pass_hat_k),
            pts(c.pass_hat_k - b.pass_hat_k),
        ),
        ("Mean cost / run", usd(b.mean_cost_usd), usd(c.mean_cost_usd), pct(d.cost_pct)),
        ("Mean tokens / run", tokens(b.mean_tokens), tokens(c.mean_tokens), pct(d.tokens_pct)),
        ("p50 latency", ms(b.p50_latency_ms), ms(c.p50_latency_ms), ""),
        ("p95 latency", ms(b.p95_latency_ms), ms(c.p95_latency_ms), pct(d.p95_latency_pct)),
        ("Runs (cases)", f"{b.runs} ({b.cases})", f"{c.runs} ({c.cases})", ""),
    ]


def headline(report: Report) -> str:
    failed = [g for g in report.gates if not g.passed]
    counts = report.label_counts
    parts = []
    for label in ("regressed", "flaky", "improved", "removed", "new", "failing"):
        if counts.get(label):
            parts.append(f"{counts[label]} {label}")
    case_bit = ", ".join(parts) if parts else "no case changed"
    if not report.gates:
        return f"No gates configured; {case_bit}."
    if failed:
        return f"{len(failed)} of {len(report.gates)} gates failed; {case_bit}."
    return f"All {len(report.gates)} gates passed; {case_bit}."


def failure_detail(stats: CaseStats | None) -> str:
    if stats is None or not stats.check_failures:
        return ""
    parts = [f"{cid} x{n}" for cid, n in stats.check_failures.items()]
    return ", ".join(parts)
