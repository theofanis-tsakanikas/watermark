-- Nothing settled may be neither in a balancing group nor in the unattributed view.
--
-- The two models split the hourly total on a `where ... is not null`, and a split is the shape
-- of mistake that loses rows without anything failing. This test returns rows when energy has
-- gone missing, which is how a dbt test signals failure — so an empty result is the assertion.

with hourly as (
    select sum(energy_wh) as total from {{ ref('settlement_hour') }}
),
grouped as (
    select coalesce(sum(energy_wh), 0) as total from {{ ref('settlement_balancing_group') }}
),
unattributed as (
    select coalesce(sum(energy_wh), 0) as total from {{ ref('unattributed_meters') }}
)

select
    hourly.total       as settled_wh,
    grouped.total      as attributed_wh,
    unattributed.total as unattributed_wh,
    hourly.total - (grouped.total + unattributed.total) as unaccounted_wh
from hourly, grouped, unattributed
where hourly.total <> grouped.total + unattributed.total
