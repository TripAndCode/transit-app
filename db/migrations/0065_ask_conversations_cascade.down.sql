ALTER TABLE ask_conversations DROP CONSTRAINT IF EXISTS ask_conversations_user_id_fkey;
ALTER TABLE ask_conversations
    ADD CONSTRAINT ask_conversations_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(user_id);
