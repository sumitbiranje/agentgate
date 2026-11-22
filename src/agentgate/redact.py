"""Secret redaction.

Traces are redacted once, right after loading, so that every later stage (checks,
judge prompts, all report formats) only ever sees the redacted copy. Redaction is
deliberately conservative: it replaces matches with a fixed marker rather than
trying to be clever about partial masking.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from agentgate.models import Trace

MARKER = "[REDACTED]"

# Built-in patterns for common credential shapes. Ordered roughly by specificity.
BUILTIN_PATTERNS: tuple[str, ...] = (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    r"\bsk-ant-[A-Za-z0-9_\-]{10,}",  # Anthropic-style API keys
    r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}",  # OpenAI-style API keys
    r"\bAKIA[0-9A-Z]{16}\b",  # AWS access key id
    r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",  # GitHub tokens
    r"\bxox[abprs]-[A-Za-z0-9\-]{10,}",  # Slack tokens
    r"(?i:\bbearer)\s+[A-Za-z0-9\-._~+/]{16,}=*",  # bearer tokens
    r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",  # JWT-like
)

BUILTIN_KEYS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "authorization",
    "client_secret",
    "private_key",
)


class Redactor:
    """Applies pattern- and key-based redaction to arbitrary JSON-like values."""

    def __init__(
        self,
        patterns: Iterable[str] = (),
        keys: Iterable[str] = (),
        *,
        use_builtins: bool = True,
    ) -> None:
        all_patterns = [*(BUILTIN_PATTERNS if use_builtins else ()), *patterns]
        self._regex = (
            re.compile("|".join(f"(?:{p})" for p in all_patterns)) if all_patterns else None
        )
        self._keys = {k.lower() for k in (*(BUILTIN_KEYS if use_builtins else ()), *keys)}

    def text(self, value: str) -> str:
        if self._regex is None:
            return value
        return self._regex.sub(MARKER, value)

    def value(self, value: Any) -> Any:
        """Redact recursively. Dict values under sensitive keys are replaced wholesale."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            out: dict[Any, Any] = {}
            for k, v in value.items():
                if isinstance(k, str) and self._is_sensitive_key(k) and v not in (None, ""):
                    out[k] = MARKER
                else:
                    out[k] = self.value(v)
            return out
        if isinstance(value, list):
            return [self.value(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.value(v) for v in value)
        return value

    def _is_sensitive_key(self, key: str) -> bool:
        normalised = key.lower().replace("-", "_")
        return normalised in self._keys

    def trace(self, trace: Trace) -> Trace:
        data = trace.model_dump(mode="json")
        # case_id and run_id are identifiers chosen by the user; leave them alone.
        keep = {"case_id": data.pop("case_id"), "run_id": data.pop("run_id")}
        redacted = self.value(data)
        return Trace.model_validate({**redacted, **keep})
