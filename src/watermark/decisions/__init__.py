"""The decision engine: what a path produces when it can, and when it cannot.

ADR-0001 is the argument and this is the implementation of it. Three rules from the doctrine
land here as code rather than as intent:

**Rule 1 — the safe state is the conservative deterministic action, not silence.** Whether
silence is safe is a property of the decision, declared in its contract, never a runtime
choice. Curtailment falls back to a proportional throttle; the anomaly path falls back to
producing nothing, and the difference is that one of them moves electricity.

**Rule 2 — a fallback is visible all the way to the end.** The marker is on the `Decision`
itself, not in a log, and every rendering carries it. A fallback that looks like a model
decision is worse than an outage, because it is silent and it teaches somebody to trust it.

**Rule 3 — anything with a significant effect on a person waits for a human.** The engine will
not construct an actuated decision for a `human_gated` path. It produces a queue entry, and the
actuation path does not exist without a recorded human decision.
"""

from __future__ import annotations

from watermark.decisions.engine import Decision, DecisionEngine, Origin, Unavailable
from watermark.decisions.fallback import proportional_throttle
from watermark.decisions.oversight import OversightQueue, Review, Verdict

__all__ = [
    "Decision",
    "DecisionEngine",
    "Origin",
    "OversightQueue",
    "Review",
    "Unavailable",
    "Verdict",
    "proportional_throttle",
]
