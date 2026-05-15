-- Phase 1-6 chatbot migrations — apply via Supabase SQL Editor BEFORE pushing
-- Phase 1-6 code. Idempotent: safe to re-run.
--
-- What this creates / adds:
--   1. chatbot_agent_settings — persistent Boss-IN/Boss-OUT mode override + reason + expiry
--   2. chatbot_agent_assets   — per-agent reusable file library (floor plans, contract templates)
--   3. chatbot_customers.email column — for email channel customer identification
--   4. chatbot_conversations.thread_keys_json + last_imap_uid — email threading
--
-- All changes are additive — no breaking changes to existing tables.

-- ============================================================================
-- 1. chatbot_agent_settings (Alembic d4e8a1b3c7f2)
-- ============================================================================

CREATE TABLE IF NOT EXISTS chatbot_agent_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(40) NOT NULL UNIQUE,
    mode_override VARCHAR(8),
    mode_reason VARCHAR(40),
    mode_reason_note TEXT,
    mode_expires_at TIMESTAMP,
    auto_mode_enabled BOOLEAN NOT NULL DEFAULT true,
    updated_by UUID REFERENCES platform_users(id),
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_chatbot_agent_settings_agent_id
    ON chatbot_agent_settings(agent_id);


-- ============================================================================
-- 2. chatbot_agent_assets (Alembic e9f1b4c8a2d6)
-- ============================================================================

CREATE TABLE IF NOT EXISTS chatbot_agent_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(40) NOT NULL,
    label VARCHAR(120) NOT NULL,
    description TEXT,
    file_url TEXT NOT NULL,
    file_kind VARCHAR(12) NOT NULL DEFAULT 'file',
    file_mime VARCHAR(80),
    keywords_json JSONB DEFAULT '[]'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT true,
    send_count INTEGER NOT NULL DEFAULT 0,
    last_sent_at TIMESTAMP,
    created_by UUID REFERENCES platform_users(id),
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_chatbot_agent_assets_agent_id
    ON chatbot_agent_assets(agent_id);

CREATE INDEX IF NOT EXISTS ix_chatbot_agent_assets_agent_enabled
    ON chatbot_agent_assets(agent_id, enabled);


-- ============================================================================
-- 3. chatbot_customers.email + chatbot_conversations email-thread fields
--    (Alembic f7c2a9d1e4b8)
-- ============================================================================

ALTER TABLE chatbot_customers
    ADD COLUMN IF NOT EXISTS email VARCHAR(254);

CREATE INDEX IF NOT EXISTS ix_chatbot_customers_email
    ON chatbot_customers(email);

ALTER TABLE chatbot_conversations
    ADD COLUMN IF NOT EXISTS thread_keys_json JSONB DEFAULT '[]'::jsonb;

ALTER TABLE chatbot_conversations
    ADD COLUMN IF NOT EXISTS last_imap_uid INTEGER;


-- ============================================================================
-- 4. Record migrations as applied in alembic_version (so future `alembic upgrade`
--    doesn't try to re-apply them)
-- ============================================================================

-- If the alembic_version table exists, mark the latest revision as applied.
-- The most recent of our 3 new revisions is f7c2a9d1e4b8.
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'alembic_version') THEN
        -- Replace whatever's there with our latest revision
        DELETE FROM alembic_version;
        INSERT INTO alembic_version (version_num) VALUES ('f7c2a9d1e4b8');
    END IF;
END $$;


-- ============================================================================
-- Done. Verify with:
--   SELECT table_name FROM information_schema.tables
--   WHERE table_name IN ('chatbot_agent_settings', 'chatbot_agent_assets');
--   -- should return 2 rows
--
--   SELECT column_name FROM information_schema.columns
--   WHERE table_name = 'chatbot_customers' AND column_name = 'email';
--   -- should return 1 row
-- ============================================================================
