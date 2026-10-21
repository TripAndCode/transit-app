-- DESTRUCTIVE: drops agg_route_forecast_ml; the next ingest restores only the
-- latest published forecast, not the earlier forecasts' rows for past dates.
DROP TABLE IF EXISTS agg_route_forecast_ml;
