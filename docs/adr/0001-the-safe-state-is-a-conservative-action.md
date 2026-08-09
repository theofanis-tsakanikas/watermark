# ADR-0001 — The safe state is a conservative action, not silence

**Status:** accepted · **Date:** 2026-08-09

## Context

Every gate in the sibling project [Attestor](../../../attestor/) defaults to refusal. A figure
that cannot be supported is not published; a report with an unresolved blocker does not
render. That is the correct default there, and it is correct for a reason worth naming: an
unpublished report has no physical consequence. The deadline is inconvenient, the auditor is
unimpressed, nothing burns.

This project inherits the instinct and must not inherit the rule.

A substation is approaching its thermal limit. The load forecast has not arrived, or its
features are past their freshness budget, or the window it depends on has not closed. Under
"the safe state is no output", the curtailment path emits nothing.

**Nothing is not a safe state here.** The chargers keep drawing. The transformer keeps
heating. The absence of a decision *is* a decision — the decision to let the current
trajectory continue — and it is the least conservative one available, taken by default, with
no record, by a component that believed it was being careful.

This is the single largest structural difference between the two projects, and getting it
wrong in either direction is expensive. Fail-open on the grid overloads a substation.
Fail-closed on the report factory publishes an unsupported number. The rule cannot be copied
across; it has to be derived from whether the action has a physical consequence.

## Decision

**Every decision path declares a fallback rule, and the fallback rule is the safe state.**

A fallback rule must satisfy four properties, all of them checkable:

1. **Deterministic.** One correct answer, computable in code, no model anywhere in it.
2. **Computable without fresh features.** It may read only measurements the path already has
   in hand. A fallback that needs the feature store is not a fallback from a feature store
   outage.
3. **Conservative in the direction of the physical hazard.** Not "the average of recent
   behaviour" — the action that costs the most and risks the least. For curtailment that is a
   proportional throttle on measured load alone: more aggressive than the model would be,
   costing customers charging speed, costing the substation nothing.
4. **Bounded.** It may only take actions the automatic path is already permitted to take. A
   fallback that disconnects a charger when the model would have throttled it has escalated
   the system's authority at the exact moment the system knows least.

**"No output" remains the safe state where the action has no physical consequence.** The
anomaly path does not actuate anything: its output is a position in an inspector's queue. If
its features are stale, the meter is not queued, and nothing in the world moves. The
settlement path publishes numbers with no immediate physical effect either; there, refusing to
state a total is right, and the total arrives later as a restatement (doctrine 4).

So the rule is not "always act". The rule is:

> The safe state is whichever of *the conservative deterministic action* and *no output*
> leaves the physical system in the better place. Which one that is, is a property of the
> decision, is declared in its contract, and is not a runtime choice.

**Every decision contract therefore declares its fallback**, and a contract with no fallback
declaration fails to load. This is the same mechanism that makes claim 4 mechanical rather
than a matter of remembering: the thing that must not be forgotten is made impossible to omit,
at load time, before any runtime path exists.

## Consequences

### The fallback marker is not optional metadata

A fallback decision and a model decision are different kinds of thing and must never be
indistinguishable. Doctrine rule 2 exists because of what happens when they are: the fallback
throttles more aggressively than the model would, an operator sees an unexplained curtailment,
and the only available conclusion is that the model is badly tuned. Nobody investigates a
feature-store outage, because the system reported a model decision. The lie is not in any
individual record; it is in the aggregate, and it trains people to distrust the model and
trust the platform, which is exactly backwards.

The marker travels to the actuator, into the decision record, onto the dashboard, and into the
metrics. **The fallback rate is a first-class SLI.** A path that has silently been running on
fallback for a week is an outage that nothing has reported.

### Falling back is a decision, and it is recorded as one

The decision record states which rule produced the output, why the primary path was
unavailable — window not closed, feature stale, forecast unavailable, model refused — and what
the primary path would have needed. This is what AI Act Art. 12 asks for in substance, and it
is also the only way to answer "how often does this actually happen?" without adding telemetry
after the fact.

### The fallback path is exercised, not merely present

An untested fallback is worse than none, because it is relied upon in the design. Claim 4's
harness drives the decision paths with stale features and asserts the fallback fires; claim
1's harness drives them with an unclosed window and asserts the same. A fallback rule with no
case in `evals/` is an unshipped fallback rule.

### It is still possible to be wrong, and that is stated

A conservative fallback throttling EV charging when it did not need to is a real cost to real
customers, and calling it "safe" only makes sense against a declared hazard. The hazard is
named in the decision contract (AI Act Art. 9), the cost of the fallback is measured, and the
fallback rate is reported. A system that never says what its caution costs is not being
honest about being cautious.

## What this rules out

- A decision path with a fallback that calls a model. That is a second primary path.
- A decision path whose fallback reads the feature store. It is not a fallback from the thing
  most likely to be broken.
- Emitting a fallback decision that the record cannot distinguish from a model decision.
- Choosing between "act conservatively" and "stay silent" at runtime, based on anything.
  The choice is a property of the decision, declared in the contract, changed in a diff, with
  a reviewer.

## Related

- Doctrine rules 1 and 2 in [CLAUDE.md](../../CLAUDE.md)
- Claim 1 (`evals/watermark/`) and claim 4 (`evals/freshness/`) — the two harnesses that make
  a fallback fire rather than merely exist
- [Attestor ADR-0001](../../../attestor/docs/adr/0001-fail-closed-with-a-recorded-key.md) —
  the same question answered the other way, for a system whose refusals cost nothing physical
