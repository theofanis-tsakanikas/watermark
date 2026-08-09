"""Feature resolution: one contract, two mechanisms that share nothing else.

ADR-0004 is the whole design of this package, and the thing it is defending against is not
laziness — it is a sensible instinct. *Do not write the feature twice.* Followed, it produces
one `compute_feature()` called by both paths and a parity harness that asserts `x == x`, runs
in eleven milliseconds, and is worth nothing.

So there are two compilers here and they are deliberately unalike:

**`offline.py`** compiles the contract into SQL and runs it as one as-of query over the raw
lakehouse. Set-oriented, recomputed from first principles, no state.

**`online.py`** compiles the same contract into an incremental aggregator the stream advances
record by record and materialises into the Feature Store. Stateful, one entity at a time.

They share the contract and `watermark.core.time`. Nothing else — and
`scripts/check_parity_paths_are_independent.py` refuses a build in which they do, because
without that gate "two mechanisms" is a sentence in a document that the first refactor to
notice the duplication will delete.
"""

from __future__ import annotations

from watermark.features.offline import OfflineResolver, as_of_sql
from watermark.features.online import OnlineMaterialiser, ServedValue
from watermark.features.parity import Divergence, compare_populations

__all__ = [
    "Divergence",
    "OfflineResolver",
    "OnlineMaterialiser",
    "ServedValue",
    "as_of_sql",
    "compare_populations",
]
