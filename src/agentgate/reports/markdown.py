"""Markdown report, sized for a pull-request comment."""

from __future__ import annotations

from agentgate.models import CaseComparison, Report
from agentgate.reports import fmt


def esc(text: str) -> str:
    """Make arbitrary text safe inside a Markdown table cell."""
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("`", "\\`")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _case_row(c: CaseComparison, compare: bool) -> str:
    label = fmt.LABEL_TEXT[c.label]
    name = f"`{esc(c.case_id)}`" + (" (critical)" if c.critical else "")
    why = (
        esc(c.candidate.sample_failure or "") if c.candidate and c.candidate.sample_failure else ""
    )
    if compare:
        return (
            f"| {label} | {name} | {fmt.runs(c.baseline)} | {fmt.runs(c.candidate)} | "
            f"{esc(fmt.failure_detail(c.candidate))} | {why} |"
        )
    detail = esc(fmt.failure_detail(c.candidate))
    return f"| {label} | {name} | {fmt.runs(c.candidate)} | {detail} | {why} |"


def render_markdown(report: Report) -> str:
    compare = report.mode == "compare"
    status = "PASS" if report.passed else "FAIL"
    icon = "✅" if report.passed else "❌"
    lines = [
        f"## {icon} agentgate {status}: {esc(report.suite)}",
        "",
        fmt.headline(report),
        "",
    ]
    if compare:
        lines += ["| Metric | Baseline | Candidate | Change |", "|---|---:|---:|---|"]
        lines += [f"| {m} | {b} | {c} | {d} |" for m, b, c, d in fmt.metric_rows(report)]
    else:
        lines += ["| Metric | Value |", "|---|---:|"]
        lines += [f"| {m} | {c} |" for m, _, c, _ in fmt.metric_rows(report)]
    lines.append("")

    if report.gates:
        lines += [
            "### Gates",
            "",
            "| | Rule | Threshold | Actual | Detail |",
            "|---|---|---|---|---|",
        ]
        for g in report.gates:
            mark = "✅" if g.passed else "❌"
            lines.append(
                f"| {mark} | `{g.rule}` | {esc(g.threshold)} | {esc(g.actual)} | {esc(g.message)} |"
            )
        lines.append("")

    header = (
        "| Label | Case | Baseline | Candidate | Failing checks | Example failure |\n"
        "|---|---|---:|---:|---|---|"
        if compare
        else (
            "| Label | Case | Runs passed | Failing checks | Example failure |\n"
            "|---|---|---:|---|---|"
        )
    )
    attention = [c for c in report.cases if c.label in fmt.ATTENTION]
    if attention:
        lines += ["### Cases to look at", "", header]
        lines += [_case_row(c, compare) for c in attention]
        lines.append("")

    lines += [
        f"<details><summary>All {len(report.cases)} cases</summary>",
        "",
        header,
    ]
    lines += [_case_row(c, compare) for c in report.cases]
    lines += ["", "</details>", ""]

    foot = [
        f"agentgate {report.tool_version}",
        f"k={report.k}",
        f"alpha={report.alpha}",
        f"seed={report.seed}",
    ]
    if report.judge:
        foot.append(f"judge: {esc(report.judge)}")
    if report.warnings:
        lines += [f"<details><summary>{len(report.warnings)} warning(s)</summary>", ""]
        lines += [f"- {esc(w)}" for w in report.warnings]
        lines += ["", "</details>", ""]
    lines.append("<sub>" + " · ".join(foot) + "</sub>")
    return "\n".join(lines) + "\n"
