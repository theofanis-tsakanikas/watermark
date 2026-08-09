"""The pure stream core.

Everything under this package is a pure function over plain data structures. It imports the
standard library and nothing else — no Flink, no boto3, no AWS SDK, no network, no clock.

That rule is not stylistic. Claims 1 to 4 are statements about windowing, watermarks,
deduplication, lateness and point-in-time joins, and a claim that needs a Flink cluster in
order to be checked is a claim nobody checks. `scripts/check_core_is_pure.py` enforces the
rule on every push, and `scripts/gate_proof.py` plants a violation to prove it still bites.

The PyFlink job in `streaming/` is an adapter: it moves records in and out and calls these
functions. It decides nothing.
"""

from __future__ import annotations
