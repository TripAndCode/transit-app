-- DESTRUCTIVE: drops users, sessions, oauth_identities, login_events and filter_presets with their rows.
DROP TABLE IF EXISTS filter_presets;
DROP TABLE IF EXISTS login_events;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS oauth_identities;
DROP TABLE IF EXISTS users;
