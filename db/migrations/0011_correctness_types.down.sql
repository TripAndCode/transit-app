ALTER TABLE agg_route_dow DROP CONSTRAINT agg_route_dow_pkey;
ALTER TABLE agg_route_dow DROP CONSTRAINT agg_route_dow_dow_iso_chk;
ALTER TABLE agg_route_dow ALTER COLUMN dow DROP NOT NULL;

ALTER TABLE agg_route_dow
  ALTER COLUMN dow TYPE TEXT USING (
    CASE dow
      WHEN 1 THEN '月' WHEN 2 THEN '火' WHEN 3 THEN '水'
      WHEN 4 THEN '木' WHEN 5 THEN '金' WHEN 6 THEN '土'
      WHEN 7 THEN '日'
    END
  );

ALTER TABLE agg_route_dow
  ADD CONSTRAINT agg_route_dow_pkey
    PRIMARY KEY (agency_id, route_code, service_type, dow);

ALTER TABLE agg_route_hour
  ALTER COLUMN scheduled_time TYPE TEXT USING (to_char(scheduled_time, 'HH24:MI:SS'));

ALTER TABLE updates
  ALTER COLUMN scheduled_time TYPE TEXT USING (to_char(scheduled_time, 'HH24:MI:SS'));
