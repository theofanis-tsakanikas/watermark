"""The PyFlink adapter. It moves records; it decides nothing.

ADR-0003 states the boundary and this package is the half that lives outside `core/`: Flink
decides *when* a function is called and with what state, and the core decides *what the answer
is*. Everything in here is wiring.

**No semantic literal appears in this package.** Every duration, grain, threshold and bound is
a name resolved from `watermark.core`, and `scripts/check_adapter_is_thin.py` refuses a numeric
literal passed to any PyFlink call. The failure mode this prevents is not a rewrite — it is one
line, `.allowed_lateness(Time.minutes(5))`, added on a Tuesday because a late reading was being
dropped, after which the core and the deployed system have quietly disagreed about what late
means and only the deployed one is right.
"""

from __future__ import annotations
