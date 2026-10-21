-- DESTRUCTIVE: drops users.deleted_at. Reapplying 0077 re-derives it only for
-- rows still holding the anonymized address, and only approximately in time.
-- Masked key addresses and revocations stay as they are.
ALTER TABLE users DROP COLUMN IF EXISTS deleted_at;
