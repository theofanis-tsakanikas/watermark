-- Claim 1, asserted in the warehouse.
--
-- The stream core cannot produce a row whose watermark precedes its own interval end — that is
-- the line the whole project is named for. This test says so again, downstream, where a
-- backfill written by hand or a row loaded from somewhere else would also have to satisfy it.
--
-- It is not redundant with the eval. The eval proves the code refuses; this proves the *table*
-- contains no counter-example, whatever put the rows there.

select
    meter_id,
    interval_start,
    closed_at,
    revision
from {{ source('silver', 'meter_interval') }}
where closed_at < interval_start + interval '15' minute
