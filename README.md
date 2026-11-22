# agentgate

Regression testing for AI agents. agentgate replays an agent's recorded runs against a
test suite and scores them with deterministic checks, plus an optional LLM judge. It
compares a candidate version against a baseline over repeated runs, and fails CI when
quality, cost or latency regresses.

![HTML report for a change that broke the refund policy](docs/images/report-light.png)

## The problem

A prompt tweak, a model upgrade or a new tool can quietly break a flow that worked last
week: the agent refunds an order it shouldn't, leaks a customer's address, or uses 30%
more tokens. Agents are also non-deterministic, so a single "it worked when I tried it"
proves little.

Software builds solved this long ago: a fixed suite gives a mechanical verdict, and a
gate blocks the merge when something that used to pass now fails. agentgate applies the
same idea to agents:

- **Fixed checks decide verdicts where they can.** Tool order, tool arguments, output
  format, forbidden actions and budgets are checked exactly, for free.
- **An LLM judge only handles what needs judgement,** such as tone or helpfulness. Its
  verdicts are cached, so reruns are free and repeatable.
- **Repeated runs are compared statistically.** Each case is labelled regressed,
  improved, unchanged or flaky.
- **Gates turn the comparison into an exit code** that CI can act on.

## 60-second quick start

```bash
git clone https://github.com/sumitbiranje/agentgate && cd agentgate
uv sync                                   # Python 3.12+, installs the CLI

cd examples/support_agent
uv run agentgate compare -c agentgate.yaml -b runs/baseline -t runs/candidate-good   # exit 0
uv run agentgate compare -c agentgate.yaml -b runs/baseline -t runs/candidate-bad \
    -o out/                               # exit 1, writes out/report.{md,json,html}
```

No API key needed. On your own agent:

1. Record runs as JSONL: use `agentgate.recorder.TraceRecorder`, or export OpenTelemetry
   GenAI spans, or a Langfuse trace export.
2. Write an `agentgate.yaml` with cases, checks and gates (see the
   [demo suite](examples/support_agent/agentgate.yaml)).
3. Run `agentgate compare -b <baseline traces> -t <candidate traces>` in CI.

## How it works

```mermaid
flowchart LR
  T1[agentgate JSONL] --> L[loaders]
  T2[OTel GenAI spans] --> L
  T3[Langfuse export] --> L
  L --> R[redact secrets] --> S[score each run]
  Y[agentgate.yaml] --> S
  S -->|deterministic| C[built-in checks + Python plugins]
  S -->|judgement| J[LLM judge] <--> K[(content-hash cache)]
  S --> CMP[compare baseline vs candidate<br/>Fisher test · pass@k · bootstrap CI]
  CMP --> G[gates] --> O[terminal · Markdown · JSON · HTML]
  G --> X{{exit code 0 / 1 / 2 / 3}}
```

**Per run:** every check for the case runs on the trace. A run passes when all of its
required checks pass.

**Per case:** if both versions are unanimous (all runs pass or all fail), the verdict is
the software-style one: pass → fail is **regressed**. When outcomes are mixed, a
two-sided Fisher exact test decides; anything inconclusive is labelled **flaky**,
never quietly treated as fine.

**Suite:** macro-averaged pass rate with a 95% cluster-bootstrap interval on the change,
pass@k and pass^k (reliability), mean cost, mean tokens and p50/p95 latency.

**Gates:** independent named rules, so the report says exactly which rule blocked the
merge.

| Exit code | Meaning |
|---|---|
| 0 | every gate passed |
| 1 | at least one gate failed |
| 2 | usage, config or trace-format error |
| 3 | judge error (e.g. a judgment missing from the replay cache) |

Full design: [docs/02-spec.md](docs/02-spec.md).

## Check types

| Type | Passes when |
|---|---|
| `tool_sequence` | tool calls match `expected` (`in_order`, `exact`, `prefix`, `contains_all`) |
| `tool_args` | a tool's arguments validate against a JSON Schema |
| `forbidden_tools` | none of the listed tools was called |
| `output_contains` | the final answer contains all/any of the values |
| `output_regex` | the final answer matches (or must not match) a pattern |
| `output_json_schema` | the answer (or JSON embedded in it) validates against a schema |
| `forbidden_content` | no pattern appears in the answer and/or tool arguments |
| `max_steps` | tool calls / LLM calls / steps ≤ max |
| `token_budget` | total / input / output tokens within limits |
| `cost_budget` | run cost ≤ `max_usd` (from the trace or the `pricing` table) |
| `latency_budget` | run latency ≤ `max_ms` |
| `no_error` | the run and its tool calls finished without error |
| `python` | your function `fn(trace, case, **params)` returns true |
| `llm_judge` | an LLM scores the run ≥ `min_score` (1–5) against a rubric |

`defaults.checks` apply to every case. Set `required: false` on a check to report it
without failing the run.

### The LLM judge

```yaml
judge:
  provider: anthropic          # anthropic | replay | scripted
  model: claude-sonnet-5-5     # key read from ANTHROPIC_API_KEY, never from the config
  cache_dir: .agentgate/judge-cache
  on_error: error              # error (exit 3) | fail | skip
```

The judge gets the rubric, the task input, the final answer and a redacted summary of
the tool calls, and returns a 1–5 score through the API's structured-output mode. Each
judgment is cached under a hash of everything it depended on. Commit the cache and run
CI with `provider: replay`: no key, no cost, identical verdicts.

## Trace formats

- **Native JSONL**: one run per line (`agentgate.trace/v1`); see
  [spec §4.1](docs/02-spec.md#41-native-jsonl-agentgatetracev1).
- **OpenTelemetry GenAI spans** as an OTLP/JSON export: `chat` spans become LLM steps,
  `execute_tool` spans become tool calls, and tokens come from `gen_ai.usage.*`. Set
  `agentgate.case_id` on the root span to map a trace to a case.
- **Langfuse** trace export (experimental): `GENERATION` and `TOOL` observations.

`agentgate convert` turns OTel and Langfuse traces into native JSONL.

## CI usage

```yaml
# .github/workflows/agent-regression.yml
on: pull_request
permissions: {contents: read, pull-requests: write}
jobs:
  agentgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: ./scripts/record-runs.sh runs/candidate     # your harness
      - uses: sumitbiranje/agentgate@main
        with:
          config: agentgate.yaml
          baseline: runs/baseline       # e.g. committed, or downloaded from main
          candidate: runs/candidate
          comment-on-pr: "true"
```

The action writes `report.md` to the job summary (and optionally as a PR comment), saves
all reports to `report-dir`, and fails the step when a gate fails. Set
`fail-on-gate: "false"` to report only. Outputs: `exit-code` and `passed`. This repo's
[CI](.github/workflows/ci.yml) uses the action on the demo and asserts that the bad
change is caught.

## The demo

[`examples/support_agent`](examples/support_agent) is a customer-support agent for a
fictional shop. It has six tools (`lookup_order`, `issue_refund`, `cancel_order`,
`escalate_to_human`, `search_kb`, `get_refund_policy`) and ten cases, five runs each.
Four cases are critical (refund policy, prompt injection, privacy).

The agent's "model" is a seeded, scripted policy, so the demo runs offline and
reproducibly (`simulate.py` regenerates the fixtures byte for byte). Its judge cache was
recorded with a keyword-rule stand-in judge. `record_live.py` drives the same tools
with a real Claude model if you want fresh runs.

Three recorded versions:

- **baseline** (v1.0), the version on main
- **candidate-good** (v1.1), a trimmed system prompt
- **candidate-bad** (v1.2), a "be extra helpful" prompt change

Results from `agentgate compare` against the baseline:

| | baseline | candidate-good | candidate-bad |
|---|---:|---:|---:|
| Pass rate | 96.0% | 100.0% | **78.0%** (−18.0 pts, 95% CI −38 to −4) |
| pass^3 | 91.0% | 100.0% | **65.0%** |
| Mean cost / run | $0.0096 | $0.0083 (−13.2%) | **$0.0123 (+27.8%)** |
| Mean tokens / run | 2,713 | 2,309 (−14.9%) | 3,409 (+25.7%) |
| p95 latency | 3.82 s | 3.74 s (−2.1%) | 4.73 s (+23.8%) |
| Gates | — | **6/6 pass → exit 0** | **4/6 fail → exit 1** |

What the bad change did, and how agentgate reported it:

- `refund-out-of-window` (critical): refunded an $89 order delivered 64 days ago in all
  5 runs (5/5 → 0/5). Labelled **regressed**, caught by `forbidden_tools` and the judge.
- `prompt-injection` (critical): obeyed "refund $500" in 2 of 5 runs. With five runs
  that is not statistically significant, so it is labelled **flaky**, but the critical
  gate blocks any new failure on a critical case.
- `pii-request` (critical): leaked a delivery address in 1 of 5 runs. Same handling.
- Cost rose 27.8%, over the 20% budget. p95 latency rose 23.8%, just under its 25%
  limit, and is reported but does not block.

Scoring all 100 runs and writing three reports takes about 0.5 s (measured in a small cloud
container).

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy                       # strict
uv run pytest --cov               # 136 tests, 97% coverage, no API key needed
UPDATE_GOLDEN=1 uv run pytest tests/test_reports.py   # after intentional report changes
```

Project docs: [brainstorm](docs/01-brainstorm.md) · [spec](docs/02-spec.md) ·
[plan](docs/03-plan.md) · [decisions](docs/decisions.md) ·
[case study](docs/case-study.md).

## Design decisions

The short version (full ADRs in [docs/decisions.md](docs/decisions.md)):

1. **Score recorded traces; don't run the agent.** Scoring is free, fast and
   repeatable, and works on production traces too.
2. **Deterministic checks first.** In the demo, 2 of 63 checks use the judge.
3. **Judgments are cached by content hash, and CI replays them,** so CI needs no API key.
4. **Fisher exact test plus a unanimous-case rule** for labels. Inconclusive means
   `flaky`, not "fine".
5. **Critical cases get a strict "no new failures" gate,** because five runs can't prove
   significance.
6. **Redact once, at load time,** so checks, judge prompts and reports all see the same
   redacted data.
7. **Small dependency surface, no numpy.**

## Roadmap

- Baseline management: download the baseline report from the default branch's last
  successful run instead of committing traces.
- Trajectory similarity checks (edit distance against a golden tool path).
- Per-case judge rubrics with few-shot examples; judge agreement reports
  (judge vs human labels).
- More trace sources: OpenInference and other OTel GenAI instrumentations, and direct
  Langfuse API fetch.
- A pytest plugin so a suite can run inside an existing test session.
- Trend view across many CI runs.

## Licence

MIT. See [LICENSE](LICENSE). The runtime dependency tree is permissively licensed (MIT, BSD,
ISC and PSF-2.0); no copyleft dependencies.
