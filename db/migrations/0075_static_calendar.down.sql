-- DESTRUCTIVE: drops static_calendar and every loaded calendar.txt row. They
-- are restored once 0075 is reapplied and each agency reloads its static feed.
DROP TABLE IF EXISTS static_calendar;
