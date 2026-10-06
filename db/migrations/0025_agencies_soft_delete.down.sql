-- DESTRUCTIVE: drops agencies.deleted_at, un-deleting every soft-deleted agency.
ALTER TABLE agencies DROP COLUMN IF EXISTS deleted_at;
