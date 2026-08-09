-- Meters with settled energy and no balancing group in force for the hour.
--
-- The other half of the previous query, and it exists because a `WHERE ... IS NOT NULL` that
-- silently drops rows is how energy leaves the books. Whatever the group totals exclude, this
-- names — so the two together account for every settled watt-hour, and the reconciliation is
-- one query rather than a subtraction somebody has to think of.
SELECT
    h.meter_id,
    h.hour_start,
    h.energy_wh,
    h.is_complete
FROM TABLE(gold.settlement_hourly(?, ?)) AS h
WHERE NOT EXISTS (
    SELECT 1
    FROM gold.meter_assignment_scd2 AS a
    WHERE a.meter_id   =  h.meter_id
      AND a.valid_from <= h.hour_start
      AND (a.valid_to IS NULL OR a.valid_to > h.hour_start)
)
ORDER BY h.hour_start, h.meter_id
