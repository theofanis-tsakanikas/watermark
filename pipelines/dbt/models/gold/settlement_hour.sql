{{
  config(
    materialized = 'incremental',
    incremental_strategy = 'merge',
    unique_key = ['meter_id', 'hour_start'],
    partitioned_by = ['day(hour_start)'],
  )
}}

-- Hourly settled energy per meter.
--
-- Must agree with `watermark.settlement.totals.settle` to the watt-hour. The two are
-- deliberately different mechanisms over one definition — the arrangement ADR-0004 argues for,
-- and the only one in which either can be checked against the other. `tests/settlement_agrees_
-- with_the_core.sql` is where that check lives.
--
-- **Merge, not insert.** A restatement changes a row that already exists; appending would leave
-- both and double-count the hour, which is the single arithmetic mistake this path exists to
-- avoid.

with latest_revision as (
    select
        meter_id,
        interval_start,
        max(revision) as revision
    from {{ source('silver', 'meter_interval') }}
    {% if is_incremental() %}
      -- Only intervals touched since the last run — including three-day-late arrivals, which
      -- is why the predicate is on the *published* time and not on the interval.
      where closed_at > (select coalesce(max(built_at), timestamp '1970-01-01') from {{ this }})
    {% endif %}
    group by meter_id, interval_start
),

settled_interval as (
    select
        w.meter_id,
        w.interval_start,
        w.energy_wh,
        w.revision,
        cardinality(w.idle_partitions) > 0 as computed_with_idle_partition
    from {{ source('silver', 'meter_interval') }} as w
    join latest_revision as l
      on  w.meter_id       = l.meter_id
      and w.interval_start = l.interval_start
      and w.revision       = l.revision
)

select
    meter_id,
    date_trunc('hour', interval_start)     as hour_start,
    sum(energy_wh)                         as energy_wh,
    count(*)                               as intervals,
    max(revision)                          as revision,
    bool_or(computed_with_idle_partition)  as computed_with_idle_partition,
    -- Four fifteen-minute intervals make an hour. An hour built from three is not a smaller
    -- total, it is a different kind of statement — and an invoice that cannot tell them apart
    -- is one nobody can defend.
    count(*) = 4                           as is_complete,
    current_timestamp                      as built_at
from settled_interval
group by meter_id, date_trunc('hour', interval_start)
