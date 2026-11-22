"""Terminal summary using rich. Falls back to plain text when not a TTY."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.text import Text

from agentgate.models import Report
from agentgate.reports import fmt

STYLE = {
    "regressed": "bold red",
    "removed": "bold red",
    "failing": "bold red",
    "flaky": "yellow",
    "improved": "green",
    "passing": "green",
    "new": "cyan",
    "unchanged": "dim",
}


def print_report(
    report: Report, console: Console | None = None, *, all_cases: bool = False
) -> None:
    con = console or Console()
    status = (
        Text(" PASS ", style="bold white on green")
        if report.passed
        else Text(" FAIL ", style="bold white on red")
    )
    con.print(Text.assemble(status, "  agentgate · ", (report.suite, "bold")))
    con.print(Text(fmt.headline(report)))

    metrics = Table(show_edge=False, box=None, pad_edge=False)
    metrics.add_column("metric", style="dim")
    if report.mode == "compare":
        metrics.add_column("baseline", justify="right")
    metrics.add_column("candidate", justify="right")
    if report.mode == "compare":
        metrics.add_column("change")
    for m, base, cand, chg in fmt.metric_rows(report):
        cells = [m, base, cand, chg] if report.mode == "compare" else [m, cand]
        metrics.add_row(*(Text(x) for x in cells))
    con.print(metrics)

    if report.gates:
        gates = Table(title="Gates", title_justify="left", show_edge=False)
        for col in ("", "rule", "threshold", "actual", "detail"):
            gates.add_column(col)
        for g in report.gates:
            gates.add_row(
                Text("✓", style="green") if g.passed else Text("✗", style="bold red"),
                g.rule,
                g.threshold,
                g.actual,
                g.message,
            )
        con.print(gates)

    shown = report.cases if all_cases else [c for c in report.cases if c.label in fmt.ATTENTION]
    if shown:
        cases = Table(
            title="Cases" if all_cases else "Cases to look at",
            title_justify="left",
            show_edge=False,
        )
        cases.add_column("label")
        cases.add_column("case")
        cases.add_column("runs")
        cases.add_column("why")
        for c in shown:
            runs = (
                fmt.runs(c.candidate)
                if c.baseline is None
                else (f"{fmt.runs(c.baseline)} -> {fmt.runs(c.candidate)}")
            )
            name = c.case_id + (" [critical]" if c.critical else "")
            why = (
                c.candidate.sample_failure
                if c.candidate and c.candidate.sample_failure
                else c.reason
            )
            cases.add_row(
                Text(fmt.LABEL_TEXT[c.label], style=STYLE[c.label]),
                Text(name),
                Text(runs),
                Text(why or ""),
            )
        con.print(cases)
    for w in report.warnings:
        con.print(Text(f"warning: {w}", style="yellow"))
