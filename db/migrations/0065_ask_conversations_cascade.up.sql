-- A user's own deletion (DELETE /api/me) takes their Ask conversations with
-- them; with no ON DELETE rule the FK would refuse to delete the user at all.
ALTER TABLE ask_conversations DROP CONSTRAINT IF EXISTS ask_conversations_user_id_fkey;
ALTER TABLE ask_conversations
    ADD CONSTRAINT ask_conversations_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;
