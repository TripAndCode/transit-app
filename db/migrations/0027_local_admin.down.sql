-- DESTRUCTIVE: drops users.password_hash; local-admin logins stop working.
ALTER TABLE users DROP COLUMN IF EXISTS password_hash;
