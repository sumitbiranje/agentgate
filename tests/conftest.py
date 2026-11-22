from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentgate.config import CaseSpec, SuiteConfig, parse_config
from agentgate.models import Step, StepType, Trace, Usage

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "support_agent"


def make_trace(
    case_id: str = "c1",
    tools: list[tuple[str, dict[str, Any]]] | None = None,
    output: str = "",
    *,
    attempt: int = 0,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cost: float | None = None,
    latency: float | None = 1000.0,
    error: str | None = None,
    model: str = "m",
    run_id: str = "",
) -> Trace:
    steps = [
        Step(type=StepType.LLM, name=model, input_tokens=input_tokens, output_tokens=output_tokens)
    ]
    for name, args in tools or []:
        steps.append(Step(type=StepType.TOOL_CALL, name=name, arguments=args))
    return Trace(
        case_id=case_id,
        run_id=run_id or f"{case_id}-{attempt}",
        attempt=attempt,
        steps=steps,
        final_output=output,
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost),
        latency_ms=latency,
        error=error,
    )


def make_config(cases: list[dict[str, Any]] | None = None, **extra: Any) -> SuiteConfig:
    base = extra.pop("_base", Path("."))
    data: dict[str, Any] = {"suite": "t", "cases": cases or [{"id": "c1"}], **extra}
    return parse_config(data, base_dir=base)


def case(config: SuiteConfig, case_id: str = "c1") -> CaseSpec:
    c = config.case(case_id)
    assert c is not None
    return c


@pytest.fixture
def demo_config() -> SuiteConfig:
    from agentgate.config import load_config

    return load_config(DEMO / "agentgate.yaml")
