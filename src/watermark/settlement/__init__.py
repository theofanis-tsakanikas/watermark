"""Settlement: hourly totals, and what happens when a number that was invoiced moves.

No model goes anywhere near this. Its entire difficulty is late data, and doctrine 4 decides
the shape of the answer: a correction never erases what was previously stated.

Two grains, one code path. The stream produces fifteen-minute windows; settlement sums four of
them into an hour and sums meters into a balancing group. A restatement at the interval grain
therefore propagates upward, and the hourly total that moves has to say what it was, what it
became and which intervals caused it — otherwise the only honest thing anybody can say about a
corrected invoice is that the number is different now.
"""

from __future__ import annotations

from watermark.settlement.restatement import SettlementRestatement, compare, net_delta_wh
from watermark.settlement.totals import (
    BalancingGroupTotal,
    HourlyTotal,
    settle,
    settle_groups,
)

__all__ = [
    "BalancingGroupTotal",
    "HourlyTotal",
    "SettlementRestatement",
    "compare",
    "net_delta_wh",
    "settle",
    "settle_groups",
]
