"""Suite/config file schema and loader.

The YAML file is validated with Pydantic. Check-specific parameters are validated by
each check's own parameter model (see ``agentgate.checks``) at load time, so a typo in a
regex or an unknown check type fails before any trace is read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agentgate.errors import ConfigError

DEFAULT_JUDGE_MODEL = "claude-sonnet-5-5"


class CheckSpec(BaseModel):
    """A check as written in the config. Extra keys are the check's parameters."""

    model_config = ConfigDict(extra="allow")

    type: str
    id: str | None = None
    required: bool = True

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class CaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str = ""
    input: Any = None
    critical: bool = False
    tags: list[str] = Field(default_factory=list)
    checks: list[CheckSpec] = Field(default_factory=list)


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[CheckSpec] = Field(default_factory=list)


class ScriptedRule(BaseModel):
    """A keyword rule for the offline ``scripted`` judge (tests and demos only)."""

    model_config = ConfigDict(extra="forbid")

    all_of: list[str] = Field(default_factory=list)
    none_of: list[str] = Field(default_factory=list)
    score_if_match: int = 5
    score_otherwise: int = 2


class JudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["anthropic", "replay", "scripted"] = "anthropic"
    model: str = DEFAULT_JUDGE_MODEL
    cache_dir: str = ".agentgate/judge-cache"
    on_error: Literal["error", "fail", "skip"] = "error"
    max_tokens: int = 512
    # rubric text -> rule, for the scripted judge
    scripted_rules: dict[str, ScriptedRule] = Field(default_factory=dict)


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float = Field(ge=0, description="USD per million input tokens")
    output: float = Field(ge=0, description="USD per million output tokens")


class RedactConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patterns: list[str] = Field(default_factory=list)
    keys: list[str] = Field(default_factory=list)
    builtins: bool = True


class GateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no_new_critical_failures: bool = False
    max_regressed_cases: int | None = Field(default=None, ge=0)
    max_pass_rate_drop: float | None = Field(default=None, ge=0, le=1)
    min_pass_rate: float | None = Field(default=None, ge=0, le=1)
    max_cost_increase_pct: float | None = None
    max_p95_latency_increase_pct: float | None = None
    max_token_increase_pct: float | None = None
    fail_on_flaky: bool = False
    fail_on_removed: bool = False


class SuiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite: str
    k: int = Field(default=3, ge=1)
    alpha: float = Field(default=0.10, gt=0, lt=1)
    seed: int = 0
    bootstrap_samples: int = Field(default=2000, ge=100)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    pricing: dict[str, ModelPrice] = Field(default_factory=dict)
    redact: RedactConfig = Field(default_factory=RedactConfig)
    defaults: Defaults = Field(default_factory=Defaults)
    cases: list[CaseSpec]
    gates: GateConfig = Field(default_factory=GateConfig)

    # Directory of the config file; relative paths resolve against it. Not from YAML.
    base_dir: Path = Field(default=Path("."), exclude=True)

    @field_validator("cases")
    @classmethod
    def _unique_case_ids(cls, cases: list[CaseSpec]) -> list[CaseSpec]:
        seen: set[str] = set()
        for case in cases:
            if case.id in seen:
                raise ValueError(f"duplicate case id {case.id!r}")
            seen.add(case.id)
        return cases

    def resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self.base_dir / p)

    def checks_for(self, case: CaseSpec) -> list[CheckSpec]:
        return [*self.defaults.checks, *case.checks]

    def case(self, case_id: str) -> CaseSpec | None:
        return next((c for c in self.cases if c.id == case_id), None)


def _format_validation_error(err: ValidationError) -> str:
    lines = []
    for e in err.errors():
        loc = ""
        for part in e["loc"]:
            loc += f"[{part}]" if isinstance(part, int) else (f".{part}" if loc else str(part))
        lines.append(f"{loc or '<root>'}: {e['msg']}")
    return "\n".join(lines)


def parse_config(data: Any, base_dir: Path = Path(".")) -> SuiteConfig:
    """Validate a config mapping, including every check's parameters."""
    if not isinstance(data, dict):
        raise ConfigError("config must be a mapping at the top level")
    try:
        config = SuiteConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc
    config.base_dir = base_dir

    from agentgate.checks import build_check  # local import: checks depend on config

    for scope, checks in [("defaults", config.defaults.checks)] + [
        (f"cases[{i}]", case.checks) for i, case in enumerate(config.cases)
    ]:
        for j, spec in enumerate(checks):
            try:
                build_check(spec, config)
            except ConfigError as exc:
                raise ConfigError(f"{scope}.checks[{j}] ({spec.type}): {exc}") from exc
    return config


def load_config(path: str | Path) -> SuiteConfig:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config {p}: {exc.strerror}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p}: invalid YAML: {exc}") from exc
    return parse_config(data, base_dir=p.resolve().parent)
