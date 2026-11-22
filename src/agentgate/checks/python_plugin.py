"""Custom checks written in Python.

``function`` is either ``"package.module:func"`` (imported normally) or
``"relative/path.py:func"`` (loaded from a file, resolved against the config's
directory). The function is called as ``func(trace, case, **params)`` and may return a
bool, a ``(bool, message)`` tuple, or a ``CheckOutcome``.

Plugins run with the same trust as your test suite: they are arbitrary code.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from collections.abc import Callable
from typing import Any

from pydantic import Field, model_validator

from agentgate.checks.base import Check, CheckContext, CheckOutcome, register
from agentgate.checks.builtin import _Params
from agentgate.config import CaseSpec, SuiteConfig
from agentgate.errors import ConfigError
from agentgate.models import Trace

PluginFn = Callable[..., Any]


class PythonParams(_Params):
    function: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _shape(self) -> PythonParams:
        if ":" not in self.function:
            raise ValueError("function must look like 'module:func' or 'file.py:func'")
        return self


def load_function(ref: str, config: SuiteConfig) -> PluginFn:
    target, _, func_name = ref.rpartition(":")
    if target.endswith(".py"):
        path = config.resolve(target)
        if not path.is_file():
            raise ConfigError(f"plugin file not found: {path}")
        digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
        mod_name = f"agentgate_plugin_{digest}"
        module = sys.modules.get(mod_name)
        if module is None:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:  # pragma: no cover - defensive
                raise ConfigError(f"cannot load plugin file {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(target)
        except ImportError as exc:
            raise ConfigError(f"cannot import plugin module {target!r}: {exc}") from exc
    fn = getattr(module, func_name, None)
    if not callable(fn):
        raise ConfigError(f"{ref}: {func_name!r} is not a callable in {target}")
    return fn  # type: ignore[no-any-return]


def coerce_outcome(value: Any) -> CheckOutcome:
    if isinstance(value, CheckOutcome):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        return CheckOutcome(bool(value[0]), str(value[1]))
    return CheckOutcome(bool(value), "" if value else "custom check returned false")


@register
class PythonCheck(Check):
    type_name = "python"
    Params = PythonParams

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        p: PythonParams = self.params  # type: ignore[assignment]
        # Import at build time so a bad reference is a config error, not a run failure.
        self._fn = load_function(p.function, self.config)

    def evaluate(self, trace: Trace, case: CaseSpec, ctx: CheckContext) -> CheckOutcome:
        p: PythonParams = self.params  # type: ignore[assignment]
        try:
            return coerce_outcome(self._fn(trace, case, **p.params))
        except Exception as exc:  # a crashing check must not look like a pass
            return CheckOutcome(False, f"custom check raised {type(exc).__name__}: {exc}")
