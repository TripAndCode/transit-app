-- users.email is an account's identity, and sign-in, invites, ADMIN_EMAILS and
-- erasure all treat an address case-insensitively, so it is stored lowercased.
-- With the existing UNIQUE (email), the CHECK makes two accounts differing only
-- in case impossible, and ON CONFLICT (email) matches across casing.
--
-- Two existing rows that differ only in case are two accounts the old rule
-- created for one address. Merging them is an operator's decision, so the
-- migration stops rather than picking one.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM users GROUP BY lower(email) HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'users holds emails that differ only in case; merge or rename those accounts first';
    END IF;
END
$$;

UPDATE users SET email = lower(email) WHERE email <> lower(email);

ALTER TABLE users ADD CONSTRAINT users_email_lowercase CHECK (email = lower(email));
