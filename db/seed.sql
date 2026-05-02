-- Test seed: 3 routes, 11 days, 5 stops
INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, scheduled_time, route_code, stop_sequence, dep_delay)
SELECT
  1,
  'seed_' || to_char(dt, 'YYYYMMDD') || '_' || rn,
  dt + (rn * interval '15 minutes'),
  service || '_' || lpad(hour_val::text,2,'0') || '時' || lpad(min_val::text,2,'0') || '分_系統' || route,
  service,
  lpad(hour_val::text,2,'0') || ':' || lpad(min_val::text,2,'0'),
  route,
  stop_seq,
  (random() * 600 - 60)::int
FROM (
  SELECT
    d.dt,
    s.service,
    t.hour_val,
    t.min_val,
    t.route,
    g.stop_seq,
    row_number() OVER () AS rn
  FROM generate_series('2026-04-20'::date, '2026-04-30'::date, '1 day') AS d(dt)
  CROSS JOIN (VALUES ('平日'), ('土日祝')) AS s(service)
  CROSS JOIN (VALUES (8,0,'5'), (12,30,'5'), (17,0,'12'), (9,15,'44'), (16,45,'44')) AS t(hour_val,min_val,route)
  CROSS JOIN generate_series(1,5) AS g(stop_seq)
) sub
ON CONFLICT DO NOTHING;

INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom)
VALUES
  (1,'S01','青森駅前',   40.8230,140.7400,ST_SetSRID(ST_MakePoint(140.7400,40.8230),4326)),
  (1,'S02','市役所前',   40.8210,140.7380,ST_SetSRID(ST_MakePoint(140.7380,40.8210),4326)),
  (1,'S03','県庁前',     40.8250,140.7360,ST_SetSRID(ST_MakePoint(140.7360,40.8250),4326)),
  (1,'S04','柳川一丁目', 40.8190,140.7430,ST_SetSRID(ST_MakePoint(140.7430,40.8190),4326)),
  (1,'S05','古川一丁目', 40.8270,140.7450,ST_SetSRID(ST_MakePoint(140.7450,40.8270),4326))
ON CONFLICT DO NOTHING;

INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time)
VALUES
  (1,'平日_08時00分_系統5',  1,'S01','08:00'),
  (1,'平日_08時00分_系統5',  2,'S02','08:08'),
  (1,'平日_08時00分_系統5',  3,'S03','08:15'),
  (1,'平日_12時30分_系統5',  1,'S01','12:30'),
  (1,'平日_12時30分_系統5',  2,'S04','12:38'),
  (1,'平日_17時00分_系統12', 1,'S02','17:00'),
  (1,'平日_17時00分_系統12', 2,'S05','17:12'),
  (1,'平日_09時15分_系統44', 1,'S03','09:15'),
  (1,'平日_16時45分_系統44', 1,'S04','16:45')
ON CONFLICT DO NOTHING;

INSERT INTO static_routes (agency_id, route_id, route_short_name)
VALUES (1,'R5','5'), (1,'R12','12'), (1,'R44','44')
ON CONFLICT DO NOTHING;
