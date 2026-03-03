-- V123: Create user_tutorial_progress table
-- Tracks per-user tutorial completion state, persists across chat sessions

CREATE TABLE user_tutorial_progress (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    tutorial_key TEXT NOT NULL,
    current_step INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    dismissed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, user_id, tutorial_key)
);

ALTER TABLE user_tutorial_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_tutorial_progress FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON user_tutorial_progress
    FOR ALL USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE INDEX idx_utp_user_tenant ON user_tutorial_progress(tenant_id, user_id);
