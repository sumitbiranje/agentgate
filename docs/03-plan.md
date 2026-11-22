# 03 — Plan

Status: T1–T25 done; T26 (push) pending · Stage 3 of 6. Checkboxes are updated as work lands.

## Milestones

| # | Milestone | Done when |
|---|---|---|
| M1 | Core model + trace loading | Native JSONL, OTel and Langfuse parse into `Trace`; redaction applied; tests green. |
| M2 | Checks + scoring | All built-in checks, Python plugins and judge check produce `CheckResult`s; tests green. |
| M3 | Statistics, comparison, gates | Labels, pass@k/pass^k, bootstrap CI, gate results and exit codes; tests green. |
| M4 | Reports + CLI | Four report formats, golden tests, Typer CLI with integration tests. |
| M5 | Demo + CI | Offline support-agent demo with three variants; composite action; CI workflow. |
| M6 | Proof + write-up | End-to-end runs, coverage ≥ 85%, screenshot, README, ADRs, case study. |

## Ordered tasks

### M1 — model and traces
- [x] T1 Project scaffold: uv, src layout, ruff, mypy strict, pytest-cov config.
      *Check*: `uv run pytest` runs (0 tests ok).
- [x] T2 `models.py`: Trace, Step, Usage, CheckResult, RunVerdict, stats/report models.
      *Check*: models round-trip JSON.
- [x] T3 `traces/jsonl.py` + `loader.py` (dirs, globbing, format detection, line errors).
      *Check*: unit tests incl. bad line → error with file:line.
- [x] T4 `traces/otel.py` (OTLP JSON, AnyValue decoding, grouping, ordering, legacy attrs).
      *Check*: fixture with chat + execute_tool spans → expected Trace.
- [x] T5 `traces/langfuse.py` (experimental). *Check*: fixture test.
- [x] T6 `redact.py`. *Check*: built-in patterns and key-based masking tested.

### M2 — checks and scoring
- [x] T7 `config.py` schema + YAML loader + path resolution + friendly errors.
- [x] T8 Check registry + 12 built-in checks. *Check*: pass and fail test per type.
- [x] T9 Python plugin check (module and file forms, exception handling).
- [x] T10 Judge: protocol, prompt, cache, replay, scripted, Anthropic provider
      (Anthropic tested with a fake client). *Check*: cache hit avoids provider call.
- [x] T11 `scoring.py`. *Check*: required vs informational checks; pricing fills cost.

### M3 — statistics, comparison, gates
- [x] T12 `stats.py`: pass@k, pass^k, Fisher exact, bootstrap, percentile.
      *Check*: hand-computed values; seeded bootstrap reproducible.
- [x] T13 `compare.py`: labels per spec §7. *Check*: table-driven tests for each label.
- [x] T14 `gates.py`: every rule + exit codes. *Check*: one test per rule, pass and fail.

### M4 — reports and CLI
- [x] T15 Markdown + JSON reports with golden files.
- [x] T16 HTML report (Jinja2, inline CSS, autoescape). *Check*: golden + escaping test.
- [x] T17 Terminal report (rich).
- [x] T18 CLI: check, compare, validate, convert, version. *Check*: CliRunner tests on
      demo fixtures; exit codes 0/1/2.

### M5 — demo and CI
- [x] T19 Demo agent (tools, scripted policy v1/v2/v3), recorder, fixture generator
      (seeded), judge cache, live recording script.
- [x] T20 Demo suite `examples/support_agent/agentgate.yaml`.
- [x] T21 Composite action `action.yml`.
- [x] T22 `.github/workflows/ci.yml`: lint, format, mypy, tests+coverage, demo gates
      (bad change must fail), action self-test.

### M6 — proof and write-up
- [x] T23 Run everything locally; fix failures; record numbers.
- [x] T24 HTML screenshot via Playwright → `docs/images/`.
- [x] T25 README, `docs/decisions.md` (ADRs), `docs/case-study.md`.
- [ ] T26 Push to GitHub (depends on repo existing) and report back.

## Decisions log (short; full ADRs in `decisions.md`)

- Python 3.12, uv, Typer, Pydantic v2, Jinja2, rich, jsonschema, PyYAML — all MIT/BSD.
- mypy strict instead of pyright: one fewer Node dependency in CI.
- No numpy/scipy: the statistics needed are small and exact (math.comb), which keeps
  install light and the maths visible.

## Progress notes

- M1–M4: done. 14 check types (the plan said 12 built-in; `no_error` was added, and the
  `python` and `llm_judge` types live in their own modules).
- Judge: switched from forced tool calls to JSON-schema structured output, because
  current models reject forced `tool_choice` (spec §6 updated; ADR-004).
- Demo: per-(variant, case, attempt) seeds, so adding a case never reshuffles other runs.
- Final local results: 136 tests, 97% coverage, ruff + strict mypy clean; demo gates:
  good change exit 0, bad change exit 1 (4/6 gates failed).
