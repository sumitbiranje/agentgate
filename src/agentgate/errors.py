"""Exception types. Each maps to a CLI exit code (see ``agentgate.gates``)."""

from __future__ import annotations


class AgentgateError(Exception):
    """Base class for expected, user-facing errors."""


class ConfigError(AgentgateError):
    """The suite/config file is invalid. Exit code 2."""


class TraceFormatError(AgentgateError):
    """A trace file could not be parsed. Exit code 2."""


class JudgeError(AgentgateError):
    """The LLM judge failed or a replay cache entry is missing. Exit code 3."""
