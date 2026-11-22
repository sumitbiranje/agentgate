from __future__ import annotations

from pathlib import Path

import pytest

from agentgate.config import load_config, parse_config
from agentgate.errors import ConfigError
from agentgate.scoring import Scorer, apply_pricing

from .conftest import DEMO, make_config, make_trace

PLUGINS = Path(__file__).parent / "data" / "plugins"


def test_demo_config_loads(demo_config) -> None:  # type: ignore[no-untyped-def]
    assert demo_config.suite == "support-agent"
    assert len(demo_config.cases) == 10
    assert demo_config.base_dir == DEMO
    assert demo_config.resolve("judge-cache") == DEMO / "judge-cache"
    assert demo_config.resolve("/abs") == Path("/abs")
    assert demo_config.case("nope") is None


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ([], "mapping"),
        ({"suite": "x"}, "cases: Field required"),
        ({"suite": "x", "cases": [{"id": "a"}, {"id": "a"}]}, "duplicate case id"),
        ({"suite": "x", "cases": [{"id": "a", "oops": 1}]}, r"cases\[0\]\.oops"),
        (
            {"suite": "x", "cases": [{"id": "a", "checks": [{"type": "max_steps"}]}]},
            r"cases\[0\]\.checks\[0\] \(max_steps\)",
        ),
        (
            {"suite": "x", "cases": [], "defaults": {"checks": [{"type": "zzz"}]}},
            r"defaults\.checks\[0\]",
        ),
        ({"suite": "x", "cases": [], "gates": {"max_pass_rate_drop": 2}}, "max_pass_rate_drop"),
    ],
)
def test_config_errors(data: object, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        parse_config(data)


def test_load_config_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("suite: [unclosed")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(bad)


def test_pricing_fills_cost() -> None:
    config = make_config(pricing={"m": {"input": 3.0, "output": 15.0}})
    t = apply_pricing(make_trace(input_tokens=1_000_000, output_tokens=100_000), config)
    assert t.usage.cost_usd == pytest.approx(4.5)
    # existing cost is kept; unknown model leaves cost unknown
    assert apply_pricing(make_trace(cost=0.1), config).usage.cost_usd == 0.1
    assert apply_pricing(make_trace(model="other"), config).usage.cost_usd is None
    no_llm = make_trace()
    no_llm.steps = []
    assert apply_pricing(no_llm, config).usage.cost_usd is None


def test_scorer_required_vs_informational_and_ids() -> None:
    config = make_config(
        [
            {
                "id": "c1",
                "checks": [
                    {"type": "output_contains", "values": ["yes"]},
                    {"type": "output_contains", "values": ["maybe"], "required": False},
                    {"type": "no_error", "id": "clean"},
                ],
            }
        ],
        defaults={"checks": [{"type": "output_contains", "values": ["y"]}]},
    )
    v = Scorer(config).score(make_trace(output="yes"))
    assert [c.check_id for c in v.checks] == [
        "output_contains",
        "output_contains#2",
        "output_contains#3",
        "clean",
    ]
    assert v.passed  # the informational failure does not fail the run
    assert not v.checks[2].passed
    assert [c.check_id for c in v.failed_checks] == []


def test_scorer_unknown_cases_warn() -> None:
    by_case, warnings = Scorer(make_config()).score_all(
        [make_trace("c1"), make_trace("zzz"), make_trace("zzz", attempt=1)]
    )
    assert len(by_case["c1"]) == 1
    assert warnings == ["2 trace(s) for unknown case 'zzz' ignored"]


def plugin_config(fn: str, **params: object) -> object:
    spec: dict[str, object] = {"type": "python", "function": fn}
    if params:
        spec["params"] = params
    return make_config([{"id": "c1", "checks": [spec]}], _base=PLUGINS)


@pytest.mark.parametrize(
    ("fn", "params", "output", "passed", "message"),
    [
        ("my_checks.py:polite", {}, "Please wait", True, ""),
        ("my_checks.py:polite", {"word": "thanks"}, "Please wait", False, "returned false"),
        ("my_checks.py:with_message", {}, "", False, "custom says no"),
        ("my_checks.py:outcome", {}, "", True, "fine"),
        ("my_checks.py:crashes", {}, "", False, "RuntimeError: bug in check"),
        ("json:loads", {}, "", False, "custom check raised"),  # importable module form
    ],
)
def test_python_plugins(
    fn: str, params: dict[str, object], output: str, passed: bool, message: str
) -> None:
    config = plugin_config(fn, **params)
    (result,) = Scorer(config).score(make_trace(output=output)).checks  # type: ignore[arg-type]
    assert result.passed is passed
    assert message in result.message


@pytest.mark.parametrize(
    ("fn", "match"),
    [
        ("my_checks.py:NOT_CALLABLE", "not a callable"),
        ("missing.py:f", "plugin file not found"),
        ("no_such_module_xyz:f", "cannot import"),
        ("nocolon", "module:func"),
    ],
)
def test_python_plugin_config_errors(fn: str, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        plugin_config(fn)
