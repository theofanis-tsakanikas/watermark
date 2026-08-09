-- Hourly settled energy per balancing group — what goes to market settlement.
--
-- The membership join is **point-in-time**: a meter that changed balancing group mid-period
-- belongs to whichever group it was in during the hour being settled, not the one it is in
-- today. Resolving it against the current row is the classic route to a market position nobody
-- took, and it is the same error `src/watermark/core/pit.py` exists to have exactly one answer
-- to. Half-open interval, `[valid_from, valid_to)`, matching that module.
--
-- A meter with no membership in force is **excluded and reported**, never bucketed into a
-- default group. An unattributed megawatt-hour is a position somebody did not take.
WITH hourly AS (
    SELECT * FROM TABLE(gold.settlement_hourly(?, ?))
),
membership AS (
    SELECT
        h.meter_id,
        h.hour_start,
        h.energy_wh,
        h.is_complete,
        h.computed_with_idle_partition,
        c.balancing_group
    FROM hourly AS h
    LEFT JOIN gold.customer_scd2 AS c
      ON  c.customer_id = (
              SELECT a.customer_id
              FROM gold.meter_assignment_scd2 AS a
              WHERE a.meter_id    =  h.meter_id
                AND a.valid_from  <= h.hour_start
                AND (a.valid_to IS NULL OR a.valid_to > h.hour_start)
          )
      AND c.valid_from <= h.hour_start
      AND (c.valid_to IS NULL OR c.valid_to > h.hour_start)
)
SELECT
    balancing_group,
    hour_start,
    SUM(energy_wh)                                          AS energy_wh,
    COUNT(*)                                                AS meters,
    ARRAY_AGG(meter_id) FILTER (WHERE NOT is_complete)      AS incomplete_meters,
    BOOL_OR(computed_with_idle_partition)                   AS computed_with_idle_partition
FROM membership
WHERE balancing_group IS NOT NULL
GROUP BY balancing_group, hour_start
ORDER BY hour_start, balancing_group
