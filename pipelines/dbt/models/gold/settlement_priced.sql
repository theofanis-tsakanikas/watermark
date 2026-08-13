{{
  config(
    materialized = 'incremental',
    incremental_strategy = 'merge',
    unique_key = ['meter_id', 'hour_start'],
    partitioned_by = ['day(hour_start)'],
  )
}}

-- Hourly settled energy, priced at the tariff that was in force during the hour.
--
-- **This model exists because the case it covers had no consumer at all.** `docs/SCENARIO.md`
-- declares "a tariff changes mid-period" as one of the four things the reference data must
-- handle, `data/cast.py` builds the SCD-2 history for it, and until now nothing read it — not a
-- query, not a model, not a test. The word `tariff` appeared in this repository only inside the
-- docstrings of `src/watermark/core/pit.py`. A declared case with no consumer cannot fail, which
-- is worse than one that fails: it looks handled, in a document, indefinitely.
--
-- **The join is point-in-time, on a half-open interval.** `M00019` moves from `STD-01` at 24
-- cents to `TOU-02` at 31 cents at 14:00. Pricing its whole day at either rate is a bill that is
-- wrong by a number nobody can see — the total is plausible, the meter reading is right, and the
-- error lives entirely in which version of a reference row was resolved. That is the failure
-- `pit.py` exists for, and settlement is where it costs money rather than accuracy.
--
-- `interval_start >= valid_from and (valid_to is null or interval_start < valid_to)` matches the
-- core's `Version.covers` exactly, including the half-open end: an interval starting at the
-- instant a tariff changes is priced at the *new* tariff. The choice matters less than the fact
-- that one definition makes it in both places.
--
-- **Priced at the interval, not at the hour.** An hour that straddles a tariff change contains
-- four intervals at two prices, and pricing the hourly total at whichever tariff happened to be
-- in force at the top of the hour would put the whole hour on one side of a boundary that runs
-- through the middle of it. The grain of the join has to be the grain of the change.
--
-- Integer cents throughout, and `energy_wh * price` divided by a million only at the end. A
-- float euro figure is a settlement figure two engines disagree about in the last place, which
-- is exactly where money lives — the same reason ADR-0004 replaced the parity tolerance with a
-- scaled integer.

with priced_intervals as (

    select
        i.meter_id,
        date_trunc('hour', i.interval_start)              as hour_start,
        i.energy_wh,
        t.tariff_code,
        t.unit_price_cents_per_kwh,
        -- Watt-hours times cents-per-kilowatt-hour is milli-cents. Kept in milli-cents through
        -- the aggregation and divided once, so no intermediate row is rounded.
        i.energy_wh * t.unit_price_cents_per_kwh          as milli_cents
    from {{ source('silver', 'meter_interval') }} as i
    join {{ source('reference', 'tariff_scd2') }} as t
      on  t.meter_id   =  i.meter_id
      and i.interval_start >= t.valid_from
      and (t.valid_to is null or i.interval_start < t.valid_to)

    {% if is_incremental() %}
      where i.interval_start >= (select coalesce(max(hour_start), timestamp '1970-01-01') from {{ this }})
    {% endif %}

)

select
    meter_id,
    hour_start,
    sum(energy_wh)                                        as energy_wh,
    -- How many distinct tariffs applied within the hour. One is the ordinary case; two is the
    -- hour the change runs through, and it is reported rather than hidden so that a bill anybody
    -- queries can be shown to have been computed across the boundary rather than on one side of
    -- it. A settlement that cannot say this cannot be argued with.
    count(distinct tariff_code)                           as tariffs_applied,
    sum(milli_cents) / 1000                               as cost_cents
from priced_intervals
group by meter_id, hour_start
