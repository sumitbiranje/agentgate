"""The one check that asks an LLM. Use it for judgement calls only (tone, helpfulness,
faithfulness), after deterministic checks have covered everything they can."""

from __future__ import annotations

from pydantic import Field

from agentgate.checks.base import Check, CheckContext, CheckOutcome, register
from agentgate.checks.builtin import _Params
from agentgate.config import CaseSpec
from agentgate.errors import JudgeError
from agentgate.judge.base import build_request
from agentgate.models import Trace


class JudgeParams(_Params):
    rubric: str = Field(min_length=1)
    min_score: int = Field(default=4, ge=1, le=5)
    include_trace: bool = True


@register
class LlmJudgeCheck(Check):
    type_name = "llm_judge"
    Params = JudgeParams

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: JudgeParams = self.params  # type: ignore[assignment]
        if ctx.judge is None:
            raise JudgeError("an llm_judge check ran without a configured judge")
        request = build_request(trace, p.rubric.strip(), p.min_score, case.input, p.include_trace)
        on_error = self.config.judge.on_error
        try:
            judgment = ctx.judge.judge(request)
        except JudgeError as exc:
            if on_error == "error":
                raise
            if on_error == "skip":
                return CheckOutcome(True, f"judge skipped: {exc}", skipped=True)
            return CheckOutcome(False, f"judge error: {exc}")
        ok = judgment.passed(p.min_score)
        msg = f"judge score {judgment.score}/5 (min {p.min_score}): {judgment.reasoning}"
        return CheckOutcome(
            ok,
            msg,
            score=float(judgment.score),
            details={"judge_id": judgment.judge_id, "cached": judgment.cached},
        )
