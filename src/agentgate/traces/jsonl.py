"""Native agentgate JSONL: one ``Trace`` object per line."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentgate.errors import TraceFormatError
from agentgate.models import TRACE_SCHEMA, Trace

_KNOWN = set(Trace.model_fields)


def parse_record(record: Any, where: str) -> Trace:
    if not isinstance(record, dict):
        raise TraceFormatError(f"{where}: expected a JSON object")
    schema = record.get("schema")
    if schema is not None and schema != TRACE_SCHEMA:
        raise TraceFormatError(f"{where}: unsupported schema {schema!r} (expected {TRACE_SCHEMA})")
    extra = {k: v for k, v in record.items() if k not in _KNOWN and k != "schema"}
    data = {k: v for k, v in record.items() if k in _KNOWN}
    if extra:
        data["metadata"] = {**extra, **(data.get("metadata") or {})}
    try:
        return Trace.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise TraceFormatError(f"{where}: {loc}: {first['msg']}") from exc


def iter_jsonl(
    path: Path, *, lenient: bool = False, warnings: list[str] | None = None
) -> Iterator[Trace]:
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            where = f"{path}:{lineno}"
            try:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise TraceFormatError(f"{where}: invalid JSON: {exc.msg}") from exc
                yield parse_record(record, where)
            except TraceFormatError as exc:
                if not lenient:
                    raise
                if warnings is not None:
                    warnings.append(f"skipped bad trace line: {exc}")


def dump_jsonl(traces: list[Trace], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for t in traces:
            record = {"schema": TRACE_SCHEMA, **t.model_dump(mode="json", exclude_defaults=False)}
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")
