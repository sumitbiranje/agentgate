"""The live recorder's tool loop, driven by a fake client (no network, no key)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agentgate.traces import load_traces

from .conftest import DEMO


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("demo_record_live", DEMO / "record_live.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["demo_record_live"] = module
    spec.loader.exec_module(module)
    return module


def msg(stop: str, *blocks: Any) -> Any:
    return SimpleNamespace(
        model="claude-opus-5-5",
        stop_reason=stop,
        content=list(blocks),
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )


def text(t: str) -> Any:
    return SimpleNamespace(type="text", text=t)


def tool_use(name: str, args: dict[str, Any]) -> Any:
    return SimpleNamespace(type="tool_use", id=f"tu_{name}", name=name, input=args)


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kw: Any) -> Any:
        self.requests.append(kw)
        user = kw["messages"][0]["content"]
        if len(kw["messages"]) == 1:
            if "1001" in user:
                return msg(
                    "tool_use",
                    text("Checking."),
                    tool_use("lookup_order", {"order_id": "1001"}),
                    tool_use("nope", {}),
                )
            if "1100" in user:
                return msg("refusal")
            return msg("tool_use", tool_use("cancel_order", {"bad_arg": 1}))
        return msg("end_turn", text("Order 1001 has shipped, tracking PP-88213."))


def test_record_live_with_fake_client(tmp_path: Path) -> None:
    module = _load()
    client = FakeClient()
    module.main(
        [
            "--out",
            str(tmp_path),
            "--attempts",
            "1",
            "--cases",
            "order-status",
            "cancel-order",
            "faq-shipping",
        ],
        client=client,
    )
    traces = {t.case_id: t for t in load_traces([tmp_path]).traces}
    t = traces["order-status"]
    assert t.tool_names == ["lookup_order", "nope"]
    assert t.final_output.startswith("Order 1001 has shipped")
    assert t.usage.total_tokens == 240
    assert traces["cancel-order"].error == "model refused"
    faq = traces["faq-shipping"]
    assert faq.tool_calls[0].error is not None  # bad args surfaced as a tool error
    # tool results were returned in one user message after the assistant turn
    second = client.requests[1]["messages"]
    assert second[1]["role"] == "assistant"
    assert [r["type"] for r in second[2]["content"]] == ["tool_result", "tool_result"]
    assert "tools" in client.requests[0]
