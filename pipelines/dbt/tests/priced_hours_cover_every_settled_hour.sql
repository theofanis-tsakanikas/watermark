-- Every settled hour must have a price, and the tariff-change hour must show two tariffs.
--
-- Two failures in one test, because they are the same failure seen from either side.
--
-- **An unpriced hour** means the point-in-time join found no tariff version in force. In a
-- settlement pipeline that row silently leaves the books: the energy was measured, delivered and
-- never billed. `settlement_priced` inner-joins the tariff deliberately rather than left-joining
-- and defaulting to zero, so the row is absent rather than free — and this test is what makes
-- absent loud.
--
-- **An hour with no tariff boundary anywhere in the day** means the join resolved every interval
-- against one version, which is what a non-point-in-time join looks like from the outside. The
-- scenario puts exactly one meter across a change at 14:00; if no hour in the whole result shows
-- two tariffs applied, the half-open interval logic collapsed and every bill is right by
-- accident.

with unpriced as (
    select h.meter_id, h.hour_start, 'no tariff in force for this hour' as problem
    from {{ ref('settlement_hour') }} as h
    left join {{ ref('settlement_priced') }} as p
      on p.meter_id = h.meter_id and p.hour_start = h.hour_start
    where p.meter_id is null
),

no_boundary as (
    select
        cast(null as varchar) as meter_id,
        cast(null as timestamp) as hour_start,
        'no hour in the whole settlement crosses a tariff change, so the point-in-time join resolved everything against one version' as problem
    where not exists (select 1 from {{ ref('settlement_priced') }} where tariffs_applied > 1)
)

select * from unpriced
union all
select * from no_boundary
