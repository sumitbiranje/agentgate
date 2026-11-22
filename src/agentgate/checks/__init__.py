"""Check types. Importing this package registers every built-in check."""

from agentgate.checks import builtin, judge_check, python_plugin
from agentgate.checks.base import (
    Check,
    CheckContext,
    CheckOutcome,
    build_check,
    register,
    registered_types,
)

__all__ = [
    "Check",
    "CheckContext",
    "CheckOutcome",
    "build_check",
    "builtin",
    "judge_check",
    "python_plugin",
    "register",
    "registered_types",
]
