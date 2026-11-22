"""Golden-file tests. Regenerate with:  UPDATE_GOLDEN=1 uv run pytest tests/test_reports.py"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentgate.config import SuiteConfig
from agentgate.models import Report
from agentgate.pipeline import run_check, run_compare
from agentgate.reports import render_html, render_json, render_markdown
from agentgate.reports.markdown import esc

from .conftest import DEMO

GOLDEN = Path(__file__).parent / "golden"
RUNS = DEMO / "runs"
FIXED_TIME = "2026-01-01T00:00:00Z"


def _normalise(report: Report) -> Report:
    report.tool_version = "0.0.0-test"
    return report


def compare_report(config: SuiteConfig, candidate: str) -> Report:
    return _normalise(
        run_compare(config, [RUNS / "baseline"], [RUNS / candidate], generated_at=FIXED_TIME)
    )


def check_golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(text, encoding="utf-8")
    assert path.exists(), f"missing golden file {name}; run with UPDATE_GOLDEN=1"
    assert text == path.read_text(encoding="utf-8"), f"{name} differs from golden file"


@pytest.mark.parametrize("candidate", ["candidate-bad", "candidate-good"])
def test_markdown_golden(demo_config: SuiteConfig, candidate: str) -> None:
    check_golden(f"{candidate}.md", render_markdown(compare_report(demo_config, candidate)))


def test_json_golden_and_schema(demo_config: SuiteConfig) -> None:
    text = render_json(compare_report(demo_config, "candidate-bad"))
    check_golden("candidate-bad.json", text)
    data = json.loads(text)
    assert data["schema"] == "agentgate.report/v1"
    assert data["exit_code"] == 1
    assert Report.model_validate(data).passed is False


def test_html_golden_and_self_contained(demo_config: SuiteConfig) -> None:
    html = render_html(compare_report(demo_config, "candidate-bad"))
    check_golden("candidate-bad.html", html)
    assert "<script" not in html
    assert "http://" not in html
    assert "https://" not in html


def test_check_mode_reports(demo_config: SuiteConfig) -> None:
    report = _normalise(run_check(demo_config, [RUNS / "baseline"], generated_at=FIXED_TIME))
    check_golden("check-baseline.md", render_markdown(report))
    assert "<html" in render_html(report)


def test_reports_are_deterministic(demo_config: SuiteConfig) -> None:
    a = render_json(compare_report(demo_config, "candidate-bad"))
    b = render_json(compare_report(demo_config, "candidate-bad"))
    assert a == b


def test_untrusted_text_is_escaped(demo_config: SuiteConfig) -> None:
    report = compare_report(demo_config, "candidate-bad")
    report.cases[0].reason = "<img src=x onerror=alert(1)> | `x`"
    assert report.cases[0].candidate is not None
    report.cases[0].candidate.sample_failure = "<b>bold</b> | pipe\nnewline"
    html = render_html(report)
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html
    md = render_markdown(report)
    assert "<b>bold</b>" not in md
    assert "\\| pipe newline" in md
    assert esc("a|b`c<d>\\") == "a\\|b\\`c&lt;d>\\\\"
    assert esc("<= 5 -> ok") == "<= 5 -> ok"
