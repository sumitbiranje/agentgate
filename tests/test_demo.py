"""The demo fixtures are generated, not hand-edited: regenerating must reproduce them."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .conftest import DEMO


def test_simulation_is_reproducible(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("demo_simulate", DEMO / "simulate.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["demo_simulate"] = module
    spec.loader.exec_module(module)
    module.main(tmp_path)
    for rel in (
        "baseline/traces.jsonl",
        "candidate-good/traces.jsonl",
        "candidate-bad/traces.jsonl",
        "baseline-otel/traces.otlp.json",
    ):
        assert (tmp_path / rel).read_bytes() == (DEMO / "runs" / rel).read_bytes(), rel
