"""Judge providers: Anthropic (real model), replay (cache only), scripted (offline rules)."""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import ValidationError

from agentgate.config import JudgeConfig, ScriptedRule, SuiteConfig
from agentgate.errors import JudgeError
from agentgate.judge.base import SYSTEM_PROMPT, Judge, JudgeRequest, Judgment, render_prompt
from agentgate.judge.cache import CachedJudge, JudgmentCache

JUDGMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "1 (fails rubric) to 5 (fully meets)"},
        "reasoning": {"type": "string", "description": "One or two sentences."},
    },
    "required": ["score", "reasoning"],
    "additionalProperties": False,
}


class AnthropicJudge:
    """LLM judge backed by the Anthropic Messages API with structured output.

    The client is injectable so tests can pass a fake; by default it is created lazily
    from the environment (``ANTHROPIC_API_KEY``), never from the config file.
    """

    def __init__(self, model: str, max_tokens: int = 1024, client: Any = None) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._client = client

    @property
    def judge_id(self) -> str:
        return f"anthropic:{self.model}"

    def _get_client(self) -> Any:
        if self._client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise JudgeError(
                    "ANTHROPIC_API_KEY is not set and the judgment is not cached. "
                    "Set the key, or use judge.provider: replay with a committed cache."
                )
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on install extras
                raise JudgeError(
                    "install the 'anthropic' extra: pip install agentgate[anthropic]"
                ) from exc
            self._client = anthropic.Anthropic()
        return self._client

    def judge(self, request: JudgeRequest) -> Judgment:
        client = self._get_client()
        prompt = render_prompt(request)
        last_error = ""
        for _attempt in range(2):  # one retry on malformed output
            try:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"format": {"type": "json_schema", "schema": JUDGMENT_SCHEMA}},
                )
            except Exception as exc:  # SDK errors already retried by the client
                raise JudgeError(f"judge API call failed: {type(exc).__name__}: {exc}") from exc
            if getattr(response, "stop_reason", None) == "refusal":
                raise JudgeError("judge model declined to grade this run (refusal)")
            text = next(
                (b.text for b in response.content if getattr(b, "type", None) == "text"), ""
            )
            try:
                data = json.loads(text)
                return Judgment(
                    score=data["score"], reasoning=data["reasoning"], judge_id=self.judge_id
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
        raise JudgeError(f"judge returned malformed output twice ({last_error})")


class ReplayJudge:
    """Only answers from the cache. Used in CI so verdicts never depend on a live model."""

    def __init__(self, model: str) -> None:
        self.model = model

    @property
    def judge_id(self) -> str:
        # Replay looks up judgments recorded by the provider named in ``model``:
        # "anthropic:claude-sonnet-5-5" or a bare model name (assumed anthropic).
        return self.model if ":" in self.model else f"anthropic:{self.model}"

    def judge(self, request: JudgeRequest) -> Judgment:
        raise JudgeError(
            f"no cached judgment for judge {self.judge_id!r} (replay mode). "
            "Record judgments once with judge.provider: anthropic and commit the cache."
        )


class ScriptedJudge:
    """Deterministic keyword rules. A stand-in for tests and offline demos, not a model.

    Rules are looked up by exact rubric text. A rule scores ``score_if_match`` when the
    final answer contains every ``all_of`` phrase and none of the ``none_of`` phrases
    (case-insensitive), else ``score_otherwise``.
    """

    def __init__(self, rules: dict[str, ScriptedRule]) -> None:
        self.rules = rules

    @property
    def judge_id(self) -> str:
        return "scripted:keyword-rules"

    def judge(self, request: JudgeRequest) -> Judgment:
        rule = self.rules.get(request.rubric.strip())
        if rule is None:
            raise JudgeError("scripted judge has no rule for this rubric")
        text = request.final_output.lower()
        missing = [w for w in rule.all_of if w.lower() not in text]
        present = [w for w in rule.none_of if w.lower() in text]
        if not missing and not present:
            return Judgment(
                score=rule.score_if_match,
                reasoning="Matches every required phrase and no forbidden phrase.",
                judge_id=self.judge_id,
            )
        bits = []
        if missing:
            bits.append("missing " + ", ".join(repr(m) for m in missing))
        if present:
            bits.append("contains " + ", ".join(repr(p) for p in present))
        return Judgment(
            score=rule.score_otherwise,
            reasoning="; ".join(bits).capitalize() + ".",
            judge_id=self.judge_id,
        )


def build_judge(config: SuiteConfig, provider: str | None = None) -> CachedJudge:
    jc: JudgeConfig = config.judge
    name = provider or jc.provider
    inner: Judge
    if name == "anthropic":
        inner = AnthropicJudge(jc.model, jc.max_tokens)
    elif name == "replay":
        inner = ReplayJudge(jc.model)
    elif name == "scripted":
        inner = ScriptedJudge(jc.scripted_rules)
    else:
        raise JudgeError(f"unknown judge provider {name!r}")
    return CachedJudge(inner, JudgmentCache(config.resolve(jc.cache_dir)))
