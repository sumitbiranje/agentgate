from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentgate.config import ScriptedRule
from agentgate.errors import JudgeError
from agentgate.judge import (
    AnthropicJudge,
    CachedJudge,
    JudgeRequest,
    Judgment,
    JudgmentCache,
    ReplayJudge,
    ScriptedJudge,
    build_judge,
)
from agentgate.judge.base import build_request, cache_key, render_prompt
from agentgate.scoring import Scorer

from .conftest import make_config, make_trace

REQ = JudgeRequest(rubric="Be polite.", min_score=4, case_input="hi", final_output="Hello there")


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def reply(text: str, stop: str = "end_turn") -> Any:
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])


def fake_client(*responses: Any) -> Any:
    return SimpleNamespace(messages=FakeMessages(list(responses)))


def test_anthropic_judge_structured_output() -> None:
    client = fake_client(reply(json.dumps({"score": 5, "reasoning": "Polite."})))
    j = AnthropicJudge("claude-sonnet-5-5", client=client).judge(REQ)
    assert (j.score, j.judge_id) == (5, "anthropic:claude-sonnet-5-5")
    (call,) = client.messages.calls
    assert call["model"] == "claude-sonnet-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in call
    assert "tool_choice" not in call
    assert "<agent_run>" in call["messages"][0]["content"]


def test_anthropic_judge_retries_once_then_fails() -> None:
    ok = fake_client(reply("not json"), reply('{"score": 3, "reasoning": "meh"}'))
    assert AnthropicJudge("m", client=ok).judge(REQ).score == 3
    bad = fake_client(reply("nope"), reply('{"score": 9, "reasoning": "x"}'))
    with pytest.raises(JudgeError, match="malformed output twice"):
        AnthropicJudge("m", client=bad).judge(REQ)


def test_anthropic_judge_refusal_and_api_error() -> None:
    with pytest.raises(JudgeError, match="refusal"):
        AnthropicJudge("m", client=fake_client(reply("", stop="refusal"))).judge(REQ)
    with pytest.raises(JudgeError, match="API call failed: RuntimeError"):
        AnthropicJudge("m", client=fake_client(RuntimeError("503"))).judge(REQ)


def test_anthropic_judge_needs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(JudgeError, match="ANTHROPIC_API_KEY is not set"):
        AnthropicJudge("m").judge(REQ)


def test_cache_hit_avoids_provider_call(tmp_path: Path) -> None:
    client = fake_client(reply('{"score": 4, "reasoning": "ok"}'))
    judge = CachedJudge(AnthropicJudge("m", client=client), JudgmentCache(tmp_path))
    first = judge.judge(REQ)
    second = judge.judge(REQ)
    assert len(client.messages.calls) == 1
    assert (judge.hits, judge.misses) == (1, 1)
    assert not first.cached
    assert second.cached
    assert second.score == 4
    # Replay finds the judgment recorded by the anthropic provider for the same model.
    replay = CachedJudge(ReplayJudge("m"), JudgmentCache(tmp_path))
    assert replay.judge(REQ).score == 4
    with pytest.raises(JudgeError, match="no cached judgment"):
        replay.judge(REQ.model_copy(update={"final_output": "changed"}))


def test_cache_key_depends_on_content_and_judge() -> None:
    k = cache_key(REQ, "a")
    assert k == cache_key(REQ.model_copy(), "a")
    assert k != cache_key(REQ, "b")
    assert k != cache_key(REQ.model_copy(update={"rubric": "Be rude."}), "a")


def test_scripted_judge() -> None:
    rule = ScriptedRule(all_of=["hello"], none_of=["bye"])
    j = ScriptedJudge({"Be polite.": rule})
    assert j.judge(REQ).score == 5
    low = j.judge(REQ.model_copy(update={"final_output": "bye"}))
    assert low.score == 2
    assert "missing 'hello'" in low.reasoning.lower()
    assert "contains 'bye'" in low.reasoning.lower()
    with pytest.raises(JudgeError, match="no rule"):
        j.judge(REQ.model_copy(update={"rubric": "other"}))


def test_prompt_and_request_building() -> None:
    t = make_trace(tools=[("lookup", {"id": 1})], output="x" * 7000)
    t.tool_calls[0].result = {"ok": True}
    t.tool_calls[0].error = "slow"
    req = build_request(t, "R", 4, {"q": 1}, include_trace=True)
    assert req.trace_summary == [
        {"tool": "lookup", "arguments": {"id": 1}, "result": '{"ok": true}', "error": "slow"}
    ]
    assert req.final_output.endswith("[truncated]")
    prompt = render_prompt(req)
    assert '{"q": 1}' in prompt
    assert "<tool_calls>" in prompt
    assert "<tool_calls>" not in render_prompt(build_request(t, "R", 4, None, include_trace=False))


def judge_config(tmp_path: Path, provider: str, on_error: str = "error") -> Any:
    rubric = "Says hello."
    return make_config(
        [{"id": "c1", "input": "hi", "checks": [{"type": "llm_judge", "rubric": rubric}]}],
        judge={
            "provider": provider,
            "cache_dir": str(tmp_path),
            "on_error": on_error,
            "model": "scripted:keyword-rules",
            "scripted_rules": {rubric: {"all_of": ["hello"]}},
        },
    )


def test_judge_check_via_scorer(tmp_path: Path) -> None:
    config = judge_config(tmp_path, "scripted")
    judge = build_judge(config)
    v = Scorer(config, judge).score(make_trace(output="hello!"))
    assert v.passed
    assert v.checks[0].score == 5
    assert "5/5" in v.checks[0].message
    fail = Scorer(config, judge).score(make_trace(output="go away"))
    assert not fail.passed
    # replay now hits the cache written by the scripted run
    replay = build_judge(config, "replay")
    assert Scorer(config, replay).score(make_trace(output="hello!")).checks[0].details["cached"]


@pytest.mark.parametrize(
    ("on_error", "passed", "skipped"), [("fail", False, False), ("skip", True, True)]
)
def test_judge_on_error_policies(
    tmp_path: Path, on_error: str, passed: bool, skipped: bool
) -> None:
    config = judge_config(tmp_path, "replay", on_error)
    v = Scorer(config, build_judge(config)).score(make_trace(output="unseen"))
    assert v.passed is passed
    assert v.checks[0].skipped is skipped


def test_judge_on_error_raises(tmp_path: Path) -> None:
    config = judge_config(tmp_path, "replay")
    with pytest.raises(JudgeError):
        Scorer(config, build_judge(config)).score(make_trace(output="unseen"))
    with pytest.raises(JudgeError, match="without a configured judge"):
        Scorer(config, None).score(make_trace(output="x"))
    with pytest.raises(JudgeError, match="unknown judge provider"):
        build_judge(config, "bogus")


def test_judgment_model() -> None:
    assert Judgment(score=4, reasoning="").passed(4)
    assert not Judgment(score=3, reasoning="").passed(4)
