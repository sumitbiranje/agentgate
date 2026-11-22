"""Report renderers. All four render the same ``Report`` object."""

from agentgate.reports.html import render_html
from agentgate.reports.json_report import render_json
from agentgate.reports.markdown import render_markdown
from agentgate.reports.terminal import print_report

RENDERERS = {
    "md": (render_markdown, "report.md"),
    "json": (render_json, "report.json"),
    "html": (render_html, "report.html"),
}

__all__ = ["RENDERERS", "print_report", "render_html", "render_json", "render_markdown"]
