"""Command-line interface.

Exit codes: 0 gates passed · 1 a gate failed · 2 usage/config/input error · 3 judge error.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from agentgate import __version__
from agentgate.checks import registered_types
from agentgate.config import SuiteConfig, load_config
from agentgate.errors import ConfigError, JudgeError, TraceFormatError
from agentgate.gates import EXIT_JUDGE, EXIT_USAGE
from agentgate.models import Report
from agentgate.pipeline import run_check, run_compare
from agentgate.reports import RENDERERS, print_report
from agentgate.traces import dump_jsonl, load_traces
from agentgate.traces.loader import Format

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Regression testing for AI agents: score recorded runs, compare, gate CI.",
)
err = Console(stderr=True)

ConfigOpt = Annotated[Path, typer.Option("--config", "-c", help="Suite file (agentgate.yaml).")]
ReportDirOpt = Annotated[
    Path | None, typer.Option("--report-dir", "-o", help="Write reports into this directory.")
]
FormatsOpt = Annotated[
    str, typer.Option("--format", "-f", help="Comma-separated report formats: md,json,html.")
]
TraceFmtOpt = Annotated[
    str, typer.Option("--trace-format", help="auto | native | otel | langfuse.")
]
LenientOpt = Annotated[
    bool, typer.Option("--lenient", help="Skip unparseable trace lines instead of failing.")
]
JudgeOpt = Annotated[
    str | None,
    typer.Option(
        "--judge-provider", help="Override judge.provider: anthropic | replay | scripted."
    ),
]
QuietOpt = Annotated[bool, typer.Option("--quiet", "-q", help="No terminal summary.")]
AllOpt = Annotated[bool, typer.Option("--all-cases", help="List every case in the terminal.")]


def _fail(message: str, code: int = EXIT_USAGE) -> typer.Exit:
    err.print(f"[bold red]error:[/] {message}", highlight=False)
    return typer.Exit(code)


def _load(config: Path) -> SuiteConfig:
    try:
        return load_config(config)
    except ConfigError as exc:
        raise _fail(f"{config}: {exc}") from exc


def _trace_format(value: str) -> Format:
    if value not in ("auto", "native", "otel", "langfuse"):
        raise _fail(f"unknown --trace-format {value!r}")
    return value  # type: ignore[return-value]


def _emit(
    report: Report, report_dir: Path | None, formats: str, quiet: bool, all_cases: bool
) -> None:
    if not quiet:
        print_report(report, Console(), all_cases=all_cases)
    if report_dir is not None:
        wanted = [f.strip() for f in formats.split(",") if f.strip()]
        unknown = [f for f in wanted if f not in RENDERERS]
        if unknown:
            raise _fail(f"unknown report format(s): {', '.join(unknown)}")
        report_dir.mkdir(parents=True, exist_ok=True)
        for f in wanted:
            render, name = RENDERERS[f]
            (report_dir / name).write_text(render(report), encoding="utf-8")
        if not quiet:
            Console().print(f"reports written to {report_dir}/ ({', '.join(wanted)})")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and report_dir is not None and "md" in formats:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write((report_dir / "report.md").read_text(encoding="utf-8"))


def _run(fn: object, *args: object, **kwargs: object) -> Report:
    try:
        return fn(*args, **kwargs)  # type: ignore[operator, no-any-return]
    except (ConfigError, TraceFormatError) as exc:
        raise _fail(str(exc)) from exc
    except JudgeError as exc:
        raise _fail(f"judge: {exc}", EXIT_JUDGE) from exc


@app.command()
def check(
    traces: Annotated[list[Path], typer.Option("--traces", "-t", help="Trace file(s) or dir(s).")],
    config: ConfigOpt = Path("agentgate.yaml"),
    report_dir: ReportDirOpt = None,
    formats: FormatsOpt = "md,json,html",
    trace_format: TraceFmtOpt = "auto",
    lenient: LenientOpt = False,
    judge_provider: JudgeOpt = None,
    quiet: QuietOpt = False,
    all_cases: AllOpt = False,
) -> None:
    """Score one set of traces. Only absolute gates apply (no baseline)."""
    cfg = _load(config)
    report = _run(
        run_check,
        cfg,
        traces,
        fmt=_trace_format(trace_format),
        lenient=lenient,
        judge_provider=judge_provider,
    )
    _emit(report, report_dir, formats, quiet, all_cases)
    raise typer.Exit(report.exit_code)


@app.command()
def compare(
    baseline: Annotated[list[Path], typer.Option("--baseline", "-b", help="Baseline traces.")],
    candidate: Annotated[list[Path], typer.Option("--candidate", "-t", help="Candidate traces.")],
    config: ConfigOpt = Path("agentgate.yaml"),
    report_dir: ReportDirOpt = None,
    formats: FormatsOpt = "md,json,html",
    trace_format: TraceFmtOpt = "auto",
    lenient: LenientOpt = False,
    judge_provider: JudgeOpt = None,
    seed: Annotated[int | None, typer.Option(help="Override the bootstrap seed.")] = None,
    quiet: QuietOpt = False,
    all_cases: AllOpt = False,
) -> None:
    """Compare candidate traces against a baseline and apply every gate."""
    cfg = _load(config)
    if seed is not None:
        cfg.seed = seed
    report = _run(
        run_compare,
        cfg,
        baseline,
        candidate,
        fmt=_trace_format(trace_format),
        lenient=lenient,
        judge_provider=judge_provider,
    )
    _emit(report, report_dir, formats, quiet, all_cases)
    raise typer.Exit(report.exit_code)


@app.command()
def validate(config: ConfigOpt = Path("agentgate.yaml")) -> None:
    """Validate the suite file, every check's parameters and plugin imports."""
    cfg = _load(config)
    n_checks = sum(len(cfg.checks_for(c)) for c in cfg.cases)
    Console().print(
        f"[green]ok[/]: suite {cfg.suite!r}: {len(cfg.cases)} cases, {n_checks} checks",
        highlight=False,
    )


@app.command()
def convert(
    inputs: Annotated[list[Path], typer.Argument(help="Trace file(s) or dir(s) to convert.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output .jsonl path.")],
    source: Annotated[
        str, typer.Option("--from", help="auto | otel | langfuse | native.")
    ] = "auto",
) -> None:
    """Convert OTel / Langfuse traces into native agentgate JSONL."""
    try:
        result = load_traces(inputs, _trace_format(source))
    except TraceFormatError as exc:
        raise _fail(str(exc)) from exc
    dump_jsonl(result.traces, output)
    Console().print(f"wrote {len(result.traces)} trace(s) to {output}", highlight=False)


@app.command("checks")
def list_checks() -> None:
    """List available check types."""
    for name in registered_types():
        typer.echo(name)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"agentgate {__version__}")


def main() -> None:  # pragma: no cover - entry point
    app()
