-- Lossless, so not marked DESTRUCTIVE: to_char(date, 'YYYY-MM-DD') is the
-- exact text the builder stored before this type change.
ALTER TABLE agg_daily_trend ALTER COLUMN date TYPE TEXT USING to_char(date, 'YYYY-MM-DD');
