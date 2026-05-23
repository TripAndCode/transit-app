-- Pre-clean: empty-string scheduled_time values are valid under the old TEXT
-- column (compute_hourly_heatmap filtered ``scheduled_time != ''`` for this
-- reason) but ``''::time`` raises. Normalise to NULL before the type change
-- so the ALTER USING cast doesn't abort on production data.
UPDATE updates SET scheduled_time = NULL WHERE scheduled_time = '';

-- updates.scheduled_time was made nullable by 0006; preserve nullability.
ALTER TABLE updates
  ALTER COLUMN scheduled_time TYPE TIME USING (NULLIF(scheduled_time, '')::time);

-- agg_route_hour.scheduled_time is part of the PK (NOT NULL); empty strings
-- would have been bugs, but ``NULLIF`` keeps the cast safe even if any slipped
-- through, surfacing them as a NOT NULL violation rather than a USING crash.
ALTER TABLE agg_route_hour
  ALTER COLUMN scheduled_time TYPE TIME USING (NULLIF(scheduled_time, '')::time);

-- agg_route_dow.dow: TEXT(Japanese) -> SMALLINT(ISODOW 1..7). Part of PK.
-- Drop PK first; rebuild after the type change.
ALTER TABLE agg_route_dow DROP CONSTRAINT agg_route_dow_pkey;

ALTER TABLE agg_route_dow
  ALTER COLUMN dow TYPE SMALLINT USING (
    CASE dow
      WHEN '月' THEN 1::smallint
      WHEN '火' THEN 2 WHEN '水' THEN 3 WHEN '木' THEN 4
      WHEN '金' THEN 5 WHEN '土' THEN 6 WHEN '日' THEN 7
      ELSE NULL  -- rollup labels like '平日' / '週末' shouldn't be persisted
    END
  );

-- Prune any stray rollup rows from earlier bugs (defensive).
DELETE FROM agg_route_dow WHERE dow IS NULL;

ALTER TABLE agg_route_dow
  ALTER COLUMN dow SET NOT NULL,
  ADD CONSTRAINT agg_route_dow_dow_iso_chk CHECK (dow BETWEEN 1 AND 7),
  ADD CONSTRAINT agg_route_dow_pkey
    PRIMARY KEY (agency_id, route_code, service_type, dow);
