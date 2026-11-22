# 01 — Brainstorm

Status: done · Stage 1 of 6

## The idea in one paragraph

Software teams stopped shipping on "it worked when I tried it" a long time ago: a
build is checked by a fixed suite, the verdict is mechanical, and a gate blocks the
merge when something that used to pass now fails. AI agents mostly still ship on
vibes. A prompt tweak, a model upgrade or a new tool can quietly break a flow that
worked last week, or double the token bill, and nobody notices until a user does.
`agentgate` applies the regression-testing discipline to agents: record what the
agent did (its trace), score each run with deterministic checks first and an LLM
judge only where judgement is genuinely needed, compare a candidate against a
baseline over repeated runs, and turn the result into a CI exit code.

## Users and the jobs they hire the tool for

| User | Job to be done | What "good" looks like |
|---|---|---|
| **Agent developer** (builds an agent with an SDK or framework) | "Tell me, before I merge, whether my prompt/model/tool change broke anything." | One command locally and in CI; a short list of cases that regressed, each with the reason; no API bill to run the checks. |
| **Platform / ML-infra team** (runs many agents for many product teams) | "Give every team the same quality gate, fed by the traces we already collect." | Works on OpenTelemetry traces they already export; config in the repo; machine-readable output for dashboards; stable exit codes. |
| **Freelancer / agency shipping agents to clients** | "Prove to the client that version 2 is not worse than version 1, and show them." | A self-contained HTML report they can email; numbers on pass rate, cost and latency; clear wording ("regressed", "improved"). |
| **Reviewer of an agent PR** | "Should I approve this?" | A Markdown comment on the PR that says pass/fail and why, in under a screen. |

Common pain across all four: agents are **non-deterministic**, so a single run proves
little; failures are often **behavioural** (called the wrong tool, skipped a lookup,
leaked data) rather than "wrong final string"; and cost and latency matter as much as
correctness.

## What exists today (honest comparison)

Based on each project's public documentation and README as of late 2026. These tools
are good and actively developed; the point is to find the gap, not to knock them.

| Tool | Strengths | Gaps for *this* job |
|---|---|---|
| **promptfoo** | YAML test suites, many providers, strong red-teaming, good CI story, side-by-side prompt comparison. | Centred on prompt→output; it typically calls the model itself. Agent trajectories (tool order, tool args) are possible but not the main model. Baseline-vs-candidate statistics over repeated runs are not a first-class concept. |
| **DeepEval** | pytest-native, large library of LLM-judged metrics (including agent/tool metrics), integrates with CI as normal tests. | Most metrics are LLM-judged, so verdicts cost money and vary between runs. Comparison of two versions and flaky detection live mainly in the hosted platform. |
| **Langfuse (evals)** | Excellent tracing and observability; datasets, experiments and LLM-as-judge evaluators run against traces. | It is a platform (self-hosted or cloud), not a CI gate. Turning an experiment into a merge-blocking exit code is left to the user. |
| **OpenAI Evals** | Early, influential registry of eval templates; hosted evals in the OpenAI platform. | Oriented to one vendor's models and hosted runs; not built around replaying arbitrary agent traces. |
| **Inspect (UK AISI)** | Rigorous framework for evaluations incl. agentic tasks and sandboxes; detailed logs; strong for research and safety evals. | It runs the tasks itself and is aimed at benchmark-style evaluation. Using it as a PR gate over production-like traces needs glue code. |

Observations:

1. Most tools **run the model** as part of the eval. That is right for benchmarks, but
   for regression testing it couples "did the behaviour change?" with "pay for and
   wait on fresh calls". Separating *recording* from *scoring* means scoring is free,
   fast and repeatable, and the same scorer works on traces from production.
2. Most tools lean on **LLM judges by default**. A judge is the right tool for "was
   the tone appropriate?", and the wrong tool for "did it call `issue_refund` before
   `lookup_order`?". Deterministic checks should decide everything they can.
3. **Repeated-run statistics** (pass@k, pass^k, confidence intervals, flaky labels)
   are discussed widely (e.g. the pass^k idea popularised by τ-bench) but rarely
   built into the gate itself.
4. **Gate semantics** (which thresholds block the merge, what exit code) are usually
   left to the user.

The gap: a small, local-first, framework-agnostic **regression gate for agent traces**.
It is not an observability platform and not a benchmark harness.

## Candidate approaches

### A. Plugin on top of an existing tool (e.g. promptfoo or DeepEval extensions)
- + Reuse a mature runner, provider list and UI.
- − Inherits the "run the model during eval" model and the judge-first metric
  design; comparison and gating would still be bolted on; a portfolio project that is
  mostly glue shows less.

### B. Hosted service / dashboard that ingests traces
- + Nice UX, history over time.
- − Heavy (DB, auth, hosting), and the core value (verdict + gate) gets buried. Teams
  that want this already have Langfuse/Phoenix/etc.

### C. Local-first CLI + library that scores recorded traces (chosen)
- Input: traces (own JSONL, OpenTelemetry GenAI spans, Langfuse export).
- Suite file declares cases and checks; deterministic checks first, LLM judge as one
  check type with a content-hash cache.
- `compare` runs the scorer on baseline and candidate traces, computes per-case and
  suite statistics, applies gate rules, writes terminal/Markdown/JSON/HTML reports and
  exits 0/1.
- Ships with a GitHub composite action.
- + Cheap, fast, reproducible, works offline, fits any agent stack that can emit
  OTel or JSONL. + Small surface, easy to reason about. − Users must record runs
  themselves (mitigated by a tiny recorder helper and an example recording script).

## Decision

**Approach C.** It matches the software-regression mental model directly: traces are
the build artefacts, checks are the test oracles, the comparison is the diff between
builds, and the gate is the merge rule. It also keeps the whole test suite and CI
free of API keys, which is a hard requirement.

Name: keep **agentgate** — it says what it does (a gate for agents). Alternatives
considered: `tracegate`, `agentdiff`, `replaycheck`. `agentgate` is the clearest.

## Non-goals (v0.1)

- Running or hosting agents; agentgate does not orchestrate the agent under test (the
  demo includes a recording script, but that is example code).
- Tracing/observability UI or long-term storage.
- Red-teaming / adversarial generation.
- A metric zoo. A few well-defined checks beat forty fuzzy ones.
- Multi-turn simulated users. Cases are single tasks; multi-turn transcripts can be
  recorded as one trace but are not generated.
- Statistical power analysis beyond what is needed to label cases honestly.

## Risks and how the design answers them

| Risk | Answer |
|---|---|
| Too few repeats → noisy labels | Deterministic rule when both sides are unanimous; Fisher exact test when not; anything inconclusive is labelled **flaky**, not silently passed. |
| Judge cost and drift | Judge is opt-in per check, cached by content hash, pinned model; CI uses replay mode. |
| Secrets leaking into reports or judge prompts | Redaction pass on every trace before scoring output is rendered or sent to a judge. |
| Trace format churn (OTel GenAI conventions are still evolving) | Parser reads both current and older attribute names; formats isolated behind one loader interface. |
