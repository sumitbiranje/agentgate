# agentgate: regression testing for AI agents

*Open-source · Python · MIT · [github.com/sumitbiranje/agentgate](https://github.com/sumitbiranje/agentgate)*

## In one line

A CLI and GitHub Action that scores an agent's recorded runs with deterministic checks
(and an LLM judge only where judgement is needed), compares a candidate version against
a baseline over repeated runs, and fails CI when quality, cost or latency regresses.

## The problem

My day job is regression testing for software builds: tracing which change affects what,
deciding verdicts with scripted checks, and gating releases on them. Agent teams mostly
lack that discipline. A one-line prompt change can make a support agent start
refunding orders outside policy, and nobody notices until a customer, or the finance
team, does.

Three things make agents harder to gate than ordinary builds:

1. **They are non-deterministic.** One passing run proves little, and one failing run
   might be noise.
2. **Failures are behavioural.** The final message can look fine while the agent called
   the wrong tool, skipped a lookup or leaked data on the way.
3. **Cost and latency are part of correctness.** A change that is 30% more expensive is
   a regression even if every answer is right.

Existing tools (promptfoo, DeepEval, Langfuse evals, OpenAI Evals, Inspect) are good, but
they are mostly built around running the model during the eval and scoring with LLM
judges. Comparing two versions over repeated runs and turning that into a merge-blocking
exit code is usually left to the user. I wanted a small, local-first gate for agent
traces.

## Approach

The same idea as build regression testing, mapped onto agents:

| Build regression testing | agentgate |
|---|---|
| Build artefact | Recorded agent run (trace) |
| Scripted test oracle | Deterministic check (tool order, tool args, schema, forbidden actions, budgets) |
| Human review for fuzzy cases | LLM judge, cached by content hash |
| Diff between builds | Baseline vs candidate comparison over repeated runs |
| Release gate | Gate rules → CI exit code |

Key choices:

- **Score recorded traces; don't run the agent.** Scoring the demo's 100 runs takes about
  half a second and costs nothing. The same scorer works on production traces exported as
  OpenTelemetry GenAI spans.
- **Deterministic first.** 14 check types: 12 deterministic, Python plugins, and an LLM
  judge. In the demo, 61 of 63 checks are deterministic.
- **The judge is reproducible.** Judgments are keyed by a SHA-256 of the prompt version,
  rubric, input, answer and tool calls. CI runs in replay mode, so it needs no API key
  and gets identical verdicts every time.
- **Honest statistics for tiny samples.** If both versions are unanimous, the verdict is
  the software-style one (pass→fail = regressed). Mixed outcomes go through a Fisher
  exact test. Anything inconclusive is labelled *flaky* rather than passed. Suite deltas
  get a seeded cluster-bootstrap confidence interval; reliability is reported as pass^k.
- **Strict where it matters.** Critical cases (refund policy, prompt injection, privacy)
  use a "no new failures" gate, because five runs can't prove significance and one leaked
  address is already too many.

## Architecture

```mermaid
flowchart LR
  T[JSONL · OTel · Langfuse] --> L[loaders] --> R[redaction] --> S[scorer]
  Y[agentgate.yaml] --> S
  S --> D[deterministic checks] & P[Python plugins] & J[LLM judge + cache]
  S --> C[comparison & statistics] --> G[gates] --> O[terminal · Markdown · JSON · HTML] & X[exit code]
```

- Python 3.12, Typer CLI, Pydantic models, Jinja2 HTML report, `uv`, ruff, strict mypy.
- About 3,500 lines in the package, 1,650 lines of tests.
- The statistics need no numpy: pass@k / pass^k estimators, Fisher's exact test,
  percentiles and the bootstrap come to about 100 lines on `math.comb`.
- Secrets are redacted once, at load time, so checks, judge prompts and every report
  format see the same redacted data.
- A composite GitHub Action writes the job summary, optionally comments on the PR, and
  maps gates to the step result.

## Results

The demo is a support agent for a fictional shop: six tools, ten cases, five runs per
case. Three recorded versions:

| | baseline | good change (trimmed prompt) | bad change ("be extra helpful") |
|---|---:|---:|---:|
| Pass rate | 96.0% | 100.0% | 78.0% (−18 pts, 95% CI −38 to −4) |
| pass^3 | 91.0% | 100.0% | 65.0% |
| Mean cost / run | $0.0096 | −13.2% | +27.8% |
| p95 latency | 3.82 s | −2.1% | +23.8% |
| Gate | — | pass (exit 0) | **fail (exit 1), 4 of 6 rules** |

For the bad change, agentgate pinpointed:

- refunds issued for an order outside the 30-day window in every run (critical,
  **regressed** 5/5 → 0/5);
- an injected "refund $500" instruction obeyed in 2 of 5 runs, and a delivery address
  leaked in 1 of 5 runs (both critical, blocked by the strict gate);
- a 27.8% cost increase against a 20% budget.

The good change passed every gate and showed its 13% cost saving.

Engineering quality: 136 tests (unit, golden-file reports, CLI integration, fixture
reproducibility), 97% branch-inclusive coverage, strict mypy, and a CI job that runs the
action on the demo and asserts that the bad change is caught. Everything runs without
an API key.

![HTML report](images/report-light.png)

## What I'd do next

- Fetch the baseline from the default branch's last green run instead of committing
  traces.
- Trajectory similarity (edit distance to a golden tool path) for agents with many
  valid paths.
- Measure judge agreement against a small set of human labels, to know when the judge
  can be trusted.
- A pytest plugin and a trend view across CI runs.

## What this shows

Taking a discipline from one domain (build regression testing: oracles, impact,
verdicts, gates) and rebuilding it cleanly for a new one. Choosing deterministic
verdicts over model calls wherever possible, being honest about uncertainty in small
samples, and shipping it as something a team can drop into CI.
