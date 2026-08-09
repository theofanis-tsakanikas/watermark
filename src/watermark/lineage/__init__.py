"""Lineage: which records a number came from, and what it replaced.

Two ideas, and both are consequences of decisions made elsewhere.

**A lineage id is derived, never generated.** `uuid4()` in an ingestion path is the obvious
implementation and it ends claim 2 on the first replay: the same events produce different ids,
so the outputs differ in bytes while agreeing in every number. `scripts/check_core_is_pure.py`
refuses `uuid4` inside the core for exactly this reason, and the same argument applies here
even though this package sits outside it. An id is therefore a hash of the thing it identifies
— replay the events and you get the same ids, because they were never anything else.

**A restatement records what moved, not just what is now true.** Doctrine 4: a correction never
erases what was previously stated. The prior value, the delta and the cause all survive, and
they survive in a record rather than in a log, because a settlement report has to be able to
print them.

This package sits outside `core/` because the core computes numbers and this computes identity.
The core never imports it — the purity gate would refuse — so ids are derived *from* core
records rather than carried *by* them.
"""

from __future__ import annotations

from watermark.lineage.identity import LineageId, derive, of_reading, of_result
from watermark.lineage.restatement import Restatement, restatements_for

__all__ = ["LineageId", "Restatement", "derive", "of_reading", "of_result", "restatements_for"]
