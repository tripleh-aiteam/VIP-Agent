-- ============================================================
-- VIP AI Platform — Full Schema for Supabase
-- Paste this entire script into: Supabase > SQL Editor > Run
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ===========================================================
-- CORE DOMAIN
-- ===========================================================

CREATE TABLE IF NOT EXISTS core_agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(120) NOT NULL UNIQUE,
    type VARCHAR(60) NOT NULL,
    version VARCHAR(30) DEFAULT '0.1.0',
    owner_team VARCHAR(120),
    endpoint_url TEXT,
    auth_type VARCHAR(30) DEFAULT 'none',
    status VARCHAR(20) DEFAULT 'active',
    is_mock BOOLEAN DEFAULT TRUE,
    capabilities_json JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS core_channels (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type VARCHAR(30) NOT NULL,
    config_json JSONB DEFAULT '{}',
    status VARCHAR(20) DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS core_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(120) NOT NULL,
    channel_id UUID NOT NULL REFERENCES core_channels(id),
    org_id VARCHAR(120),
    session_key VARCHAR(255) UNIQUE NOT NULL,
    context_json JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ===========================================================
-- ORCHESTRATION DOMAIN
-- ===========================================================

CREATE TABLE IF NOT EXISTS orch_task_definitions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_type VARCHAR(100) NOT NULL UNIQUE,
    target_agent_type VARCHAR(60) NOT NULL,
    input_schema_json JSONB DEFAULT '{}',
    output_schema_json JSONB DEFAULT '{}',
    timeout_seconds INTEGER DEFAULT 300,
    requires_judgement BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);

CREATE TABLE IF NOT EXISTS orch_task_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_definition_id UUID NOT NULL REFERENCES orch_task_definitions(id),
    initiator_type VARCHAR(30) NOT NULL,
    initiator_id VARCHAR(120),
    source_channel VARCHAR(30),
    target_agent_id UUID REFERENCES core_agents(id),
    trace_id VARCHAR(64),
    input_payload JSONB DEFAULT '{}',
    output_payload JSONB,
    status VARCHAR(20) DEFAULT 'pending',
    error_message TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_orch_task_runs_trace_id ON orch_task_runs(trace_id);

CREATE TABLE IF NOT EXISTS orch_schedule_rules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(120) NOT NULL UNIQUE,
    cron_expr VARCHAR(60) NOT NULL,
    target_task_definition_id UUID NOT NULL REFERENCES orch_task_definitions(id),
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);

CREATE TABLE IF NOT EXISTS orch_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_type VARCHAR(60) NOT NULL,
    source_run_ids_json JSONB DEFAULT '[]',
    content_json JSONB DEFAULT '{}',
    delivery_channel VARCHAR(30),
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);

-- ===========================================================
-- AUDIT DOMAIN
-- ===========================================================

CREATE TABLE IF NOT EXISTS audit_judgement_cases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_run_id UUID NOT NULL REFERENCES orch_task_runs(id),
    rule_result VARCHAR(30),
    model_result VARCHAR(30),
    risk_score FLOAT,
    decision VARCHAR(30),
    evidence_json JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_approval_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    judgement_case_id UUID NOT NULL REFERENCES audit_judgement_cases(id),
    requested_by VARCHAR(120) NOT NULL,
    approved_by VARCHAR(120),
    decision VARCHAR(20),
    decided_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_event_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source VARCHAR(120) NOT NULL,
    event_type VARCHAR(60) NOT NULL,
    trace_id VARCHAR(64),
    payload_json JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_event_logs_trace_id ON audit_event_logs(trace_id);

-- ===========================================================
-- A2A (Agent-to-Agent) DOMAIN
-- ===========================================================

CREATE TABLE IF NOT EXISTS a2a_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sender_agent_id UUID NOT NULL REFERENCES core_agents(id),
    target_agent_id UUID NOT NULL REFERENCES core_agents(id),
    task_run_id UUID REFERENCES orch_task_runs(id),
    trace_id VARCHAR(64),
    message_type VARCHAR(30) NOT NULL,
    envelope_json JSONB DEFAULT '{}',
    status VARCHAR(20) DEFAULT 'sent',
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_a2a_messages_trace_id ON a2a_messages(trace_id);

-- ===========================================================
-- AGENT-OPS DOMAIN
-- ===========================================================

CREATE TABLE IF NOT EXISTS agent_heartbeats (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID NOT NULL REFERENCES core_agents(id),
    status VARCHAR(20) NOT NULL,
    latency_ms INTEGER,
    metadata_json JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);

CREATE TABLE IF NOT EXISTS realty_spatial_capture_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID NOT NULL REFERENCES core_agents(id),
    device_id VARCHAR(120),
    property_ref VARCHAR(255),
    video_uri TEXT,
    audio_uri TEXT,
    model_3d_uri TEXT,
    metadata_json JSONB DEFAULT '{}',
    processing_status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);

-- ===========================================================
-- TELEGRAM DOMAIN
-- ===========================================================

CREATE TABLE IF NOT EXISTS telegram_users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    telegram_user_id VARCHAR(60) UNIQUE NOT NULL,
    linked_user_id VARCHAR(120),
    role VARCHAR(30) DEFAULT 'viewer',
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_actions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    telegram_user_id VARCHAR(60) NOT NULL,
    action_type VARCHAR(60) NOT NULL,
    related_task_run_id UUID REFERENCES orch_task_runs(id),
    payload_json JSONB DEFAULT '{}',
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);

-- ===========================================================
-- SEED DATA
-- ===========================================================

-- Mock agents
INSERT INTO core_agents (name, type, version, owner_team, endpoint_url, auth_type, status, is_mock, capabilities_json)
VALUES
    ('mock-asset-agent', 'asset', '0.1.0', 'vip-core', 'http://localhost:9010', 'none', 'active', true, '{"actions":["fetch_summary","evaluate_portfolio"]}'),
    ('mock-stock-agent', 'stock', '0.1.0', 'vip-core', 'http://localhost:9011', 'none', 'active', true, '{"actions":["fetch_market_data","analyze_trends"]}'),
    ('mock-realty-agent', 'realty', '0.1.0', 'vip-core', 'http://localhost:9012', 'none', 'active', true, '{"actions":["fetch_listings","spatial_capture"]}')
ON CONFLICT (name) DO NOTHING;

-- Default channels
INSERT INTO core_channels (type, config_json, status)
VALUES
    ('web', '{"origin":"http://localhost:3000"}', 'active'),
    ('telegram', '{"bot_token_env":"TELEGRAM_BOT_TOKEN"}', 'active'),
    ('slack', '{}', 'inactive'),
    ('whatsapp', '{}', 'inactive'),
    ('ai_glass', '{}', 'planned');

-- Task definitions
INSERT INTO orch_task_definitions (task_type, target_agent_type, input_schema_json, output_schema_json, timeout_seconds, requires_judgement)
VALUES
    ('asset_summary', 'asset', '{"type":"object","properties":{"portfolio_id":{"type":"string"}}}', '{"type":"object","properties":{"summary":{"type":"object"}}}', 120, false),
    ('stock_analysis', 'stock', '{"type":"object","properties":{"symbols":{"type":"array"}}}', '{"type":"object","properties":{"analysis":{"type":"object"}}}', 180, true),
    ('realty_listing_fetch', 'realty', '{"type":"object","properties":{"region":{"type":"string"}}}', '{"type":"object","properties":{"listings":{"type":"array"}}}', 300, false)
ON CONFLICT (task_type) DO NOTHING;

-- Default telegram admin
INSERT INTO telegram_users (telegram_user_id, linked_user_id, role, status)
VALUES ('admin_000', 'system', 'admin', 'active')
ON CONFLICT (telegram_user_id) DO NOTHING;

-- ===========================================================
-- DONE! Check Table Editor to see all 15 tables with seed data.
-- ===========================================================
