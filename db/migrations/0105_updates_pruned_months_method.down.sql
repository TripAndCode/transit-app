-- DESTRUCTIVE: drops the method each drop was recorded under; exports reaching dates before a retention floor then name that method as unknown.
ALTER TABLE updates_pruned_months DROP COLUMN IF EXISTS method_version;
