# Design decisions (ADRs)

Short records of the choices that shape agentgate. Each one says what was decided,
why, and what it costs.

## ADR-001: Score recorded traces; don't run the agent

**Decision.** agentgate consumes traces that something else recorded (the agent's own
test harness, OpenTelemetry, Langfuse). It never calls the agent.

**Why.** It separates "did behaviour change?" from "pay for and wait on fresh model
calls". Scoring is then free, takes under a second for the demo's 100 runs, and gives
the same result every time. The same scorer also works on traces sampled from production.

**Cost.** Users must record runs. Mitigated by a 100-line `recorder.py` helper, a
working live-recording example, and support for OTel traces that many stacks already emit.

## ADR-002: Deterministic checks first, LLM judge only for judgement

**Decision.** 12 deterministic check types plus Python plugins carry the suite.
`llm_judge` is one check type among them, opt-in per case.

**Why.** "Did it call `issue_refund` for an order outside the window?" has an exact
answer, so a judge is the wrong tool: it costs money, is slower, and can disagree with
itself. A judge is right for tone and helpfulness. In the demo, 2 of 63 checks are
judged.

**Cost.** Writing good deterministic checks takes thought. The schema-based checks
(`tool_args`, `output_json_schema`) keep that manageable.

## ADR-003: Judgments are cached by content hash; CI replays

**Decision.** Every judgment is stored under the SHA-256 of {prompt version, judge id,
rubric, threshold, case input, final output, tool-call summary}. The `replay` provider
answers only from that cache and treats a miss as an error.

**Why.** Reruns cost nothing and give identical verdicts. Committing the cache makes CI
reproducible and keeps API keys out of CI. A changed output (or prompt) is a new key, so
stale judgments are never reused.

**Cost.** The cache must be refreshed when outputs change. A miss fails with a clear
error and exit code 3.

## ADR-004: Structured output, not forced tool calls, for the judge

**Decision.** The Anthropic judge uses the Messages API's JSON-schema structured output
(`output_config.format`) and validates with Pydantic, retrying once on malformed
output. It sends no sampling parameters.

**Why.** Current models reject a forced `tool_choice`, and reproducibility comes from
the cache (ADR-003), not from temperature.

## ADR-005: Case labels from a deterministic rule plus Fisher's exact test

**Decision.** If both versions are unanimous (all runs pass, or all fail), the verdict
is the software-style one: pass→fail is `regressed`. If outcomes are mixed, a two-sided
Fisher exact test at `alpha` (default 0.10) decides `regressed`/`improved`. Otherwise
the case is `flaky`.

**Why.** It works with a single run (the common CI case) and stays honest with
repeated runs. Fisher's test is exact for the tiny samples agents produce, where
normal approximations break down. Inconclusive results are labelled `flaky`, never
silently reported as fine.

**Cost.** With five runs per side, a 5/5 → 4/5 change is not significant (p = 1.0).
That is why critical cases have a stricter gate (ADR-006).

## ADR-006: Gates are explicit, independent rules

**Decision.** Each gate is a named rule with a threshold (critical failures, regressed
case count, pass-rate drop, cost, p95 latency, tokens, flaky, removed). Exit codes are
0 (pass), 1 (gate failed), 2 (usage/config/input error), 3 (judge error).

**Why.** Reviewers can see exactly which rule blocked a merge and why. Critical cases
use "no new failures": any failing candidate run on a case that always passed before
blocks the merge, regardless of statistics. Distinct exit codes let CI tell "your
change is worse" apart from "the tool could not run".

## ADR-007: Suite pass rate is macro-averaged; CI is a cluster bootstrap

**Decision.** Suite pass rate is the mean of per-case pass rates. The 95% interval on
the change is a seeded percentile bootstrap that resamples whole cases (2,000 draws).

**Why.** Each case counts equally however many times it was run. Resampling cases
rather than runs respects that runs of the same case are correlated. The fixed seed
makes reports byte-identical across reruns (tested).

## ADR-008: Redact once, at load time

**Decision.** Built-in credential patterns, user patterns and sensitive keys are
redacted immediately after loading, before any check, judge prompt or report sees
the trace.

**Why.** One choke point is easier to trust than per-renderer escaping. Checks see the
same data the reports show, so a check can never pass because it saw a secret that the
report hides.

## ADR-009: Small dependency surface; no numpy

**Decision.** Runtime dependencies are Pydantic, PyYAML, Jinja2, jsonschema, rich and
Typer (MIT/BSD; their transitive dependencies add ISC and PSF-2.0, all permissive). The `anthropic` SDK is an optional extra. The statistics use
`math.comb` and `random`.

**Why.** The tool installs fast in CI, and the statistics stay short enough to read
and test against known reference values.

## ADR-010: Offline demo with a scripted policy, labelled as such

**Decision.** The demo agent's "model" is a seeded, scripted policy. Its judge cache was
recorded with a keyword-rule `scripted` judge. Both are documented as stand-ins.
`record_live.py` drives the same tools with a real Claude model when a key is present.

**Why.** The whole suite, the demo and CI must run with no API key. Using a stand-in
rather than presenting canned output as real model output keeps the demo honest.
