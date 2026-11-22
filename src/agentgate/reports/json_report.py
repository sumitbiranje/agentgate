"""Machine-readable report: the ``Report`` model as JSON (schema agentgate.report/v1)."""

from __future__ import annotations

import json

from agentgate.models import Report


def render_json(report: Report) -> str:
    data = report.model_dump(mode="json", by_alias=True)
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
