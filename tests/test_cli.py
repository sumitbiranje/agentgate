"""Integration tests: the real CLI on the committed demo fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentgate.cli import app

from .conftest import DEMO

runner = CliRunner()
CFG = str(DEMO / "agentgate.yaml")
RUNS = DEMO / "runs"


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def test_compare_baseline_vs_itself_passes() -> None:
    code, out = invoke(
        "compare", "-c", CFG, "-b", str(RUNS / "baseline"), "-t", str(RUNS / "baseline")
    )
    assert code == 0, out
    assert "PASS" in out


def test_compare_good_change_passes(tmp_path: Path) -> None:
    code, out = invoke(
        "compare",
        "-c",
        CFG,
        "-b",
        str(RUNS / "baseline"),
        "-t",
        str(RUNS / "candidate-good"),
        "-o",
        str(tmp_path),
        "-q",
    )
    assert code == 0, out
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["passed"] is True
    assert data["delta"]["cost_pct"] < 0


def test_compare_bad_change_fails_with_clear_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code, out = invoke(
        "compare",
        "-c",
        CFG,
        "-b",
        str(RUNS / "baseline"),
        "-t",
        str(RUNS / "candidate-bad"),
        "-o",
        str(tmp_path / "r"),
        "--all-cases",
    )
    assert code == 1, out
    assert "FAIL" in out
    assert "refund-out-of-window" in out
    assert {p.name for p in (tmp_path / "r").iterdir()} == {
        "report.md",
        "report.json",
        "report.html",
    }
    data = json.loads((tmp_path / "r" / "report.json").read_text())
    labels = {c["case_id"]: c["label"] for c in data["cases"]}
    assert labels["refund-out-of-window"] == "regressed"
    failed = {g["rule"] for g in data["gates"] if not g["passed"]}
    assert {"no_new_critical_failures", "max_regressed_cases", "max_cost_increase_pct"} <= failed
    assert "agentgate FAIL" in summary.read_text()


def test_otel_baseline_gives_same_result(tmp_path: Path) -> None:
    code, _ = invoke(
        "compare",
        "-c",
        CFG,
        "-b",
        str(RUNS / "baseline-otel"),
        "-t",
        str(RUNS / "candidate-bad"),
        "-o",
        str(tmp_path),
        "-f",
        "json",
        "-q",
    )
    assert code == 1
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["baseline"]["pass_rate"] == pytest.approx(0.96)


def test_check_command() -> None:
    code, out = invoke("check", "-c", CFG, "-t", str(RUNS / "candidate-good"))
    assert code == 0, out
    code, out = invoke("check", "-c", CFG, "-t", str(RUNS / "candidate-bad"), "-q")
    assert code == 1  # critical cases not passing every run


def test_usage_errors_exit_2(tmp_path: Path) -> None:
    assert invoke("check", "-c", str(tmp_path / "nope.yaml"), "-t", str(RUNS))[0] == 2
    assert invoke("check", "-c", CFG, "-t", str(tmp_path / "missing"))[0] == 2
    assert invoke("check", "-c", CFG, "-t", str(RUNS / "baseline"), "--trace-format", "xml")[0] == 2
    code, out = invoke(
        "check", "-c", CFG, "-t", str(RUNS / "baseline"), "-o", str(tmp_path), "-f", "pdf", "-q"
    )
    assert code == 2
    assert "unknown report format" in out


def test_judge_error_exit_3(tmp_path: Path) -> None:
    cfg = tmp_path / "s.yaml"
    cfg.write_text(
        "suite: s\njudge: {provider: replay, cache_dir: cache}\n"
        "cases:\n  - id: refund-out-of-window\n    checks:\n      - {type: llm_judge, rubric: r}\n"
    )
    code, out = invoke("check", "-c", str(cfg), "-t", str(RUNS / "baseline"), "-q")
    assert code == 3
    assert "no cached judgment" in out


def test_validate_convert_checks_version(tmp_path: Path) -> None:
    code, out = invoke("validate", "-c", CFG)
    assert code == 0
    assert "10 cases" in out
    code, out = invoke("convert", str(RUNS / "baseline-otel"), "-o", str(tmp_path / "x.jsonl"))
    assert code == 0, out
    assert len((tmp_path / "x.jsonl").read_text().splitlines()) == 50
    assert invoke("convert", str(tmp_path / "nope"), "-o", str(tmp_path / "y.jsonl"))[0] == 2
    code, out = invoke("checks")
    assert "tool_sequence" in out
    code, out = invoke("version")
    assert out.startswith("agentgate ")


def test_seed_override_changes_only_ci(tmp_path: Path) -> None:
    for seed in ("1", "2"):
        invoke(
            "compare",
            "-c",
            CFG,
            "-b",
            str(RUNS / "baseline"),
            "-t",
            str(RUNS / "candidate-bad"),
            "-o",
            str(tmp_path / seed),
            "-f",
            "json",
            "-q",
            "--seed",
            seed,
        )
    a = json.loads((tmp_path / "1" / "report.json").read_text())
    b = json.loads((tmp_path / "2" / "report.json").read_text())
    assert a["delta"]["pass_rate"] == b["delta"]["pass_rate"]
    assert a["seed"] == 1
    assert b["seed"] == 2
