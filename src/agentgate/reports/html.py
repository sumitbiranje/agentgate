"""Single-file HTML report: inline CSS, no scripts, no external requests."""

from __future__ import annotations

from dataclasses import dataclass

from jinja2 import Environment, PackageLoader, select_autoescape

from agentgate.models import Report
from agentgate.reports import fmt


@dataclass
class Card:
    k: str
    v: str
    d: str = ""
    cls: str = ""


def _cls(change: float | None, higher_is_bad: bool) -> str:
    if change is None or abs(change) < 1e-9:
        return ""
    worse = change > 0 if higher_is_bad else change < 0
    return "bad" if worse else "good"


def _cards(r: Report) -> list[Card]:
    c, b, d = r.candidate, r.baseline, r.delta
    if b is None or d is None:
        return [
            Card("Pass rate", fmt.rate(c.pass_rate)),
            Card(f"pass^{r.k}", fmt.rate(c.pass_hat_k)),
            Card("Mean cost / run", fmt.usd(c.mean_cost_usd)),
            Card("p95 latency", fmt.ms(c.p95_latency_ms)),
            Card("Runs", str(c.runs), f"{c.cases} cases"),
        ]
    ci = ""
    if d.pass_rate_ci is not None:
        ci = f" · CI {d.pass_rate_ci.low * 100:+.0f}…{d.pass_rate_ci.high * 100:+.0f}"
    return [
        Card(
            "Pass rate",
            fmt.rate(c.pass_rate),
            f"{fmt.pts(d.pass_rate)} vs {fmt.rate(b.pass_rate)}{ci}",
            _cls(d.pass_rate, False),
        ),
        Card(
            f"pass^{r.k}",
            fmt.rate(c.pass_hat_k),
            f"was {fmt.rate(b.pass_hat_k)}",
            _cls(c.pass_hat_k - b.pass_hat_k, False),
        ),
        Card(
            "Mean cost / run",
            fmt.usd(c.mean_cost_usd),
            f"{fmt.pct(d.cost_pct)} vs {fmt.usd(b.mean_cost_usd)}",
            _cls(d.cost_pct, True),
        ),
        Card(
            "p95 latency",
            fmt.ms(c.p95_latency_ms),
            f"{fmt.pct(d.p95_latency_pct)} vs {fmt.ms(b.p95_latency_ms)}",
            _cls(d.p95_latency_pct, True),
        ),
        Card(
            "Mean tokens / run",
            fmt.tokens(c.mean_tokens),
            f"{fmt.pct(d.tokens_pct)}",
            _cls(d.tokens_pct, True),
        ),
    ]


_env = Environment(
    loader=PackageLoader("agentgate.reports", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_html(report: Report) -> str:
    template = _env.get_template("report.html.j2")
    return template.render(
        r=report,
        cards=_cards(report),
        headline=fmt.headline(report),
        fmt=fmt,
        labels=fmt.LABEL_TEXT,
    )
