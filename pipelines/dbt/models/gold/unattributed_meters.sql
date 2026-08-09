{{ config(materialized = 'view') }}

-- Settled energy with no balancing group in force for the hour.
--
-- This model exists because a `where balancing_group is not null` that silently drops rows is
-- how energy leaves the books. Whatever the group totals exclude, this names — and the test
-- beside it asserts the two sum back to the hourly total, so the reconciliation cannot rot.

select
    h.meter_id,
    h.hour_start,
    h.energy_wh,
    h.is_complete
from {{ ref('settlement_hour') }} as h
left join {{ source('reference', 'meter_assignment_scd2') }} as a
  on  a.meter_id   =  h.meter_id
  and a.valid_from <= h.hour_start
  and (a.valid_to is null or a.valid_to > h.hour_start)
where a.meter_id is null
