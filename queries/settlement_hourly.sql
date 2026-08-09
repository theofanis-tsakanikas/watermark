-- Hourly settled energy per meter, from the published interval windows.
--
-- The offline half of the settlement path. It reads the same Iceberg table the stream writes
-- and must agree with `watermark.settlement.totals.settle` to the watt-hour — the two are
-- deliberately different mechanisms over one definition, which is the arrangement ADR-0004
-- argues for and the only one in which either can be checked against the other.
--
-- **Parameters are bound, never interpolated.** `?` placeholders throughout. A settlement query
-- built by string concatenation is a settlement query somebody will one day build from a
-- customer-supplied meter id.
--
-- **The newest revision wins, and nothing else does.** A published window and its restatement
-- are two rows for one interval; summing both double-counts it, which is the single arithmetic
-- mistake this whole path exists to avoid.
WITH latest_revision AS (
    SELECT
        meter_id,
        interval_start,
        MAX(revision) AS revision
    FROM gold.meter_interval
    WHERE interval_start >= ?
      AND interval_start <  ?
    GROUP BY meter_id, interval_start
),
settled_interval AS (
    SELECT
        w.meter_id,
        w.interval_start,
        w.energy_wh,
        w.revision,
        -- Carried up rather than dropped: a total computed while a substation was excluded
        -- from the watermark has a hole in it, and the invoice has to be able to say so.
        CARDINALITY(w.idle_partitions) > 0 AS computed_with_idle_partition
    FROM gold.meter_interval AS w
    JOIN latest_revision AS l
      ON  w.meter_id       = l.meter_id
      AND w.interval_start = l.interval_start
      AND w.revision       = l.revision
)
SELECT
    meter_id,
    DATE_TRUNC('hour', interval_start)          AS hour_start,
    SUM(energy_wh)                              AS energy_wh,
    COUNT(*)                                    AS intervals,
    MAX(revision)                               AS revision,
    BOOL_OR(computed_with_idle_partition)       AS computed_with_idle_partition,
    -- Four fifteen-minute intervals make an hour. An hour built from three is not a smaller
    -- total, it is a different kind of statement, and the flag is what lets a reader tell them
    -- apart without recounting.
    COUNT(*) = 4                                AS is_complete
FROM settled_interval
GROUP BY meter_id, DATE_TRUNC('hour', interval_start)
ORDER BY hour_start, meter_id
