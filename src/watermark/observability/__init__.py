"""Cost per decision and cost per meter, as first-class metrics.

`CLAUDE.md` says these are not an afterthought, and the reason is specific to this system: the
three expensive resources bill for as long as they *exist*, not for what they do. A platform
whose cost is dominated by idle capacity cannot be reasoned about from an invoice — the invoice
is nearly the same whether it took a million decisions or none.

So the unit costs are declared here, from published pricing, dated, and the arithmetic is
offline. Nothing in this module reads a bill; it reads a rate card and a count.
"""

from __future__ import annotations

from watermark.observability.cost import CostModel, CostReport, estimate

__all__ = ["CostModel", "CostReport", "estimate"]
