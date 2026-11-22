"""Check protocol and registry.

A check is a small class with a Pydantic ``Params`` model and an ``evaluate`` method.
Parameters are validated when the config loads, so evaluation can assume they are
well-formed. Checks are pure functions of (trace, case) plus an optional judge.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from agentgate.errors import ConfigError
from agentgate.models import CheckResult, Trace

if TYPE_CHECKING:
    from agentgate.config import CaseSpec, CheckSpec, SuiteConfig
    from agentgate.judge.base import Judge


@dataclass(frozen=True)
class CheckOutcome:
    """What a check returns. ``message`` should say *why* in one line."""

    passed: bool
    message: str = ""
    score: float | None = None
    skipped: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckContext:
    """Run-time dependencies a check may need. Only the judge check uses one today."""

    config: SuiteConfig
    judge: Judge | None = None


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Check(ABC):
    type_name: ClassVar[str]
    Params: ClassVar[type[BaseModel]] = NoParams

    def __init__(self, spec: CheckSpec, params: BaseModel, config: SuiteConfig) -> None:
        self.spec = spec
        self.params = params
        self.config = config

    @abstractmethod
    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        """Score one trace."""

    def run(self, trace: Trace, case: CaseSpec, ctx: CheckContext, check_id: str) -> CheckResult:
        outcome = self.evaluate(trace, case, ctx)
        return CheckResult(
            check_id=check_id,
            type=self.type_name,
            passed=outcome.passed,
            required=self.spec.required,
            skipped=outcome.skipped,
            message=outcome.message,
            score=outcome.score,
            details=outcome.details,
        )


_REGISTRY: dict[str, type[Check]] = {}


def register(cls: type[Check]) -> type[Check]:
    """Class decorator that makes a check available by its ``type_name``."""
    if cls.type_name in _REGISTRY:
        raise ValueError(f"check type {cls.type_name!r} already registered")
    _REGISTRY[cls.type_name] = cls
    return cls


def registered_types() -> list[str]:
    return sorted(_REGISTRY)


def build_check(spec: CheckSpec, config: SuiteConfig) -> Check:
    cls = _REGISTRY.get(spec.type)
    if cls is None:
        raise ConfigError(
            f"unknown check type {spec.type!r}; known types: {', '.join(registered_types())}"
        )
    try:
        params = cls.Params.model_validate(spec.params)
    except ValidationError as exc:
        msgs = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'params'}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(msgs) from exc
    return cls(spec, params, config)
