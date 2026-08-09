# ADR-0003 — The pure-core boundary, and how core↔Flink equivalence is established

**Status:** accepted · **Date:** 2026-08-09

## Context

`CLAUDE.md` states the rule: all stream logic lives in `src/watermark/core/` as pure functions
over plain data, importing no Flink and no cloud SDK, and the PyFlink job in `streaming/` is a
thin adapter. `scripts/check_core_is_pure.py` enforces the import and ambient-state half of
that, and `make gate-proof` attacks it.

That leaves the hard half unanswered, and it is the one that decides whether claim 1 means
anything.

**The core reimplements event-time semantics.** Watermark generation, window assignment,
triggering, allowed lateness. Claim 1 — *no decision comes out of a window that has not
closed* — is proved by driving that reimplementation with labelled cases. If the
reimplementation's semantics differ from Flink's, then claim 1 is a true statement about a toy
engine and a false impression about the system that runs. Nothing in a pure pytest run can
detect the difference, because the thing it would have to compare against needs a JVM.

Two bad answers are available and both are common:

- **Move the tests into Flink.** Then every claim needs a cluster, the suite takes minutes
  instead of milliseconds, a stranger cannot check anything on a laptop, and the evidence
  stops being evidence.
- **Assert the equivalence in prose.** "The adapter is thin" in a README, with nothing
  checking it, until the afternoon somebody adds `allowed_lateness(Time.minutes(5))` to the
  job because a late reading was being dropped, and the core and the deployed system have
  disagreed ever since.

## Decision

Three parts: a stated contract for the core, a structural prohibition on the adapter, and two
test tiers with different costs and different guarantees.

### 1. What the core owns, and what it does not

**The core owns every decision.** Given the same inputs it must produce the same outputs as
the deployed job, and it is the definition of what the right answer is:

- normalisation across firmware schema variants
- deduplication on `(meter_id, interval_start, payload_hash)`
- watermark generation from observed event times, including **idle-source detection**
- window assignment and the rule for **when a window is closed**
- allowed lateness, and what goes to the late side output
- clock-skew quarantine, with the reason
- point-in-time SCD-2 resolution
- freshness judgement, fallback selection, and decision assembly

**The core does not own the mechanics of running.** Checkpointing, state backends, partition
assignment, serialisation, network, retries, backpressure, recovery from a savepoint. Those
are Flink's, they are genuinely hard, and buying them is the reason Flink is here at all. The
core has no opinion about them.

The seam is therefore precise: **Flink decides when a function is called and with what state;
the core decides what the answer is.** Claim 1 is a statement about answers, so the core is
where it is proved. Claim 1's *delivery* also depends on Flink calling the function at the
right moment, and that is what tier 2 below exists to check.

### 2. The adapter carries no numbers

The failure mode is not a rewrite of the logic in `streaming/`. It is one literal. Flink's
convenience constructors are designed to make a semantic decision easy to express inline:

```python
WatermarkStrategy.for_bounded_out_of_orderness(Duration.of_seconds(5))   # a policy decision
.window(TumblingEventTimeWindows.of(Time.minutes(15)))                    # a grain decision
.allowed_lateness(Time.minutes(5))                                        # a lateness decision
```

Every one of those is a decision the core is supposed to own, written where no test looks.

**The rule: no semantic literal appears in `streaming/`.** Every duration, threshold, grain and
bound passed to a PyFlink call is a name resolved from `watermark.core`. A gate,
`scripts/check_adapter_is_thin.py`, will enforce it when `streaming/` arrives in Phase 1:

- `streaming/` may import `pyflink` and `watermark.core`, and nothing else from `watermark`.
- No numeric or duration literal may be an argument to a `pyflink` call.
- The watermark strategy must be constructed from a core-supplied generator, not from
  `for_bounded_out_of_orderness` or `for_monotonous_timestamps`.

It ships with the mutation that breaks it — planting `Time.minutes(15)` in the job and
requiring the named gate to refuse — in the same commit, per `docs/DECISIONS.md` decision 14.

The rule is mechanical, and being mechanical is the point: "keep the adapter thin" is advice,
and advice loses to a deadline.

### 3. Two test tiers

| | `make test` | `make test-flink` |
|---|---|---|
| Directory | `tests/` | `tests_flink/` |
| Needs | Python 3.12 only | a JVM and `apache-flink` |
| Runtime | under a second | minutes |
| Runs | every save, every push | its own CI job, every push |
| Proves | every claim, against the core | that Flink agrees with the core |

They are separate directories rather than a pytest marker so that `make test` **cannot**
accidentally pick the slow tier up, and so that a reader can see at a glance which claims cost
a JVM. `testpaths` in `pyproject.toml` names `tests` only.

**Tier 2 is the equivalence test.** It runs the real PyFlink job on a local MiniCluster over
the same fixtures the core harness uses, and asserts the outputs are byte-identical — the same
comparison claim 2 already makes about replays, applied across engines instead of across runs.
Its labelled cases are claim 1's: the idle substation, the stalled watermark, the burst, the
window that must not close, the window that must.

**A skip is not a pass.** Locally, tier 2 skips with a printed reason when no JVM is present,
because a check nobody can run is a check nobody runs. In CI it must execute: with
`WATERMARK_REQUIRE_FLINK=1` set, a missing JVM or a missing `apache-flink` is a failure, not a
skip. Silent skipping is how a suite reports green for a year while proving one thing less
than it says.

**The Flink version is pinned to the deployed runtime.** The local `apache-flink` minor version
and the `runtime_environment` in `infra/streaming/` must match, checked by
`scripts/check_flink_versions_agree.py`. An equivalence test against a different Flink than
the one in production establishes equivalence with something nobody is running. Managed
Service for Apache Flink supports Flink 2.3 as of 2026-07 and Flink 2.2 as of 2026-03; Flink
2.2 defaults to Python 3.12, which is this repository's floor. The version is chosen in Phase
1, in `docs/AWS-CONSTRAINTS.md`, and the two places that state it are compared by a script.

## Consequences

- Claim 1 is proved twice, at two prices: exhaustively and instantly against the core, and once
  end to end against Flink. Neither alone would be enough. The core alone proves a model; the
  MiniCluster alone is too slow to hold the number of labelled cases claim 1 needs.
- Adding a case to claim 1 costs nothing. Adding it to the equivalence tier costs minutes, so
  the equivalence tier carries the *shapes* of the pathologies, not every variation of them.
- If PyFlink's operator surface ever forces a decision into the framework, the answer is to
  move that decision **out** into the core and pass it in — never to move the tests in. The
  adapter gate is what makes that the path of least resistance rather than a good intention.
- `pyproject.toml` gains a `flink` optional extra. It is never a hard dependency: `make test`,
  every claim gate and `make preflight --fast` must keep running on a machine with no JVM.

## Related

- `src/watermark/gates/core_purity.py` — the import and ambient-state half, already enforced
- Claim 1 (`evals/watermark/`) and claim 2 (`evals/replay/`)
- `docs/AWS-CONSTRAINTS.md` — the Managed Flink facts the pinned version is chosen from
