"""Find trace files, detect their format and parse them.

Detection looks at content, not file names: an object with ``resourceSpans`` is OTLP,
one with ``observations`` is Langfuse, anything else is native agentgate JSONL.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agentgate.errors import TraceFormatError
from agentgate.models import Trace
from agentgate.traces.jsonl import iter_jsonl, parse_record
from agentgate.traces.langfuse import trace_from_langfuse
from agentgate.traces.otel import traces_from_otlp

Format = Literal["auto", "native", "otel", "langfuse"]
SUFFIXES = {".jsonl", ".json", ".ndjson"}


@dataclass
class LoadResult:
    traces: list[Trace] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)


def find_files(paths: Iterable[str | Path]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.is_file() and f.suffix in SUFFIXES))
        elif p.is_file():
            files.append(p)
        else:
            raise TraceFormatError(f"trace path not found: {p}")
    if not files:
        raise TraceFormatError("no trace files found (looked for *.jsonl, *.json, *.ndjson)")
    return files


def _detect(obj: Any) -> Format:
    sample = obj[0] if isinstance(obj, list) and obj else obj
    if isinstance(sample, dict):
        if "resourceSpans" in sample:
            return "otel"
        if "observations" in sample:
            return "langfuse"
    return "native"


def _read_json_docs(path: Path) -> list[Any]:
    """A .json file is one document; .jsonl/.ndjson is one document per line."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TraceFormatError(f"{path}: invalid JSON: {exc.msg} (line {exc.lineno})") from exc
        return doc if isinstance(doc, list) else [doc]
    docs = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            try:
                docs.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise TraceFormatError(f"{path}:{lineno}: invalid JSON: {exc.msg}") from exc
    return docs


def _peek(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        if path.suffix == ".json":
            try:
                return json.load(fh)
            except json.JSONDecodeError as exc:
                raise TraceFormatError(f"{path}: invalid JSON: {exc.msg}") from exc
        for line in fh:
            if line.strip():
                try:
                    return json.loads(line)
                except json.JSONDecodeError as exc:
                    raise TraceFormatError(f"{path}:1: invalid JSON: {exc.msg}") from exc
    return None


def load_file(
    path: Path, fmt: Format = "auto", *, lenient: bool = False, warnings: list[str] | None = None
) -> list[Trace]:
    kind = _detect(_peek(path)) if fmt == "auto" else fmt
    if kind == "native":
        if path.suffix == ".json":
            return [
                parse_record(rec, f"{path}[{i}]") for i, rec in enumerate(_read_json_docs(path))
            ]
        return list(iter_jsonl(path, lenient=lenient, warnings=warnings))
    docs = _read_json_docs(path)
    if kind == "otel":
        return traces_from_otlp(docs, str(path))
    return [trace_from_langfuse(d, f"{path}[{i}]") for i, d in enumerate(docs)]


def load_traces(
    paths: Iterable[str | Path], fmt: Format = "auto", *, variant: str = "", lenient: bool = False
) -> LoadResult:
    result = LoadResult(files=find_files(paths))
    for f in result.files:
        result.traces.extend(load_file(f, fmt, lenient=lenient, warnings=result.warnings))
    seen: dict[tuple[str, str], int] = {}
    for i, t in enumerate(result.traces):
        if variant:
            t.variant = variant
        if not t.run_id:
            t.run_id = f"{t.case_id}#{t.attempt}#{i}"
        key = (t.case_id, t.run_id)
        if key in seen:
            result.warnings.append(f"duplicate run_id {t.run_id!r} for case {t.case_id!r}")
        seen[key] = i
    return result
