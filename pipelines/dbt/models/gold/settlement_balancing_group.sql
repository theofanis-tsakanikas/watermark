{{
  config(
    materialized = 'incremental',
    incremental_strategy = 'merge',
    unique_key = ['balancing_group', 'hour_start'],
    partitioned_by = ['day(hour_start)'],
  )
}}

-- Hourly settled energy per balancing group — what goes to market settlement.
--
-- The membership join is **point-in-time**, on a half-open interval matching
-- `src/watermark/core/pit.py`. A meter that changed group mid-period belongs to whichever group
-- it was in during the hour being settled; resolving against the current row is the classic
-- route to a market position nobody took, and it is the same error the core has exactly one
-- answer to.
--
-- A meter with no membership in force is excluded here and reported by `unattributed_meters`.
-- The two together account for every settled watt-hour, so the reconciliation is a query rather
-- than a subtraction somebody has to think of.

with attributed as (
    select
        h.meter_id,
        h.hour_start,
        h.energy_wh,
        h.is_complete,
        h.computed_with_idle_partition,
        c.balancing_group
    from {{ ref('settlement_hour') }} as h
    left join {{ source('reference', 'meter_assignment_scd2') }} as a
      on  a.meter_id    =  h.meter_id
      and a.valid_from  <= h.hour_start
      and (a.valid_to is null or a.valid_to > h.hour_start)
    left join {{ source('reference', 'customer_scd2') }} as c
      on  c.customer_id =  a.customer_id
      and c.valid_from  <= h.hour_start
      and (c.valid_to is null or c.valid_to > h.hour_start)
)

select
    balancing_group,
    hour_start,
    sum(energy_wh)                                             as energy_wh,
    count(*)                                                   as meters,
    -- Named, not counted. The question after a short total is always *which* meters, and a
    -- count sends somebody back to the data to find out.
    array_agg(meter_id) filter (where not is_complete)         as incomplete_meters,
    bool_or(computed_with_idle_partition)                      as computed_with_idle_partition,
    current_timestamp                                          as built_at
from attributed
where balancing_group is not null
group by balancing_group, hour_start
