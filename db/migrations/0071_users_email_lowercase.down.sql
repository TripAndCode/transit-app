-- The addresses stay lowercased; only the rule that keeps them so is dropped.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_lowercase;
