-- V130: Team Invitations + User Permission Overrides

CREATE TABLE team_invitations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(255) NOT NULL,
    email           VARCHAR(255) NOT NULL,
    name            VARCHAR(255),
    role_id         UUID NOT NULL REFERENCES roles(id),
    module_overrides JSONB DEFAULT NULL,
    invite_token    VARCHAR(64) NOT NULL UNIQUE,
    expires_at      TIMESTAMPTZ NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','accepted','declined','expired','revoked')),
    invited_by      TEXT NOT NULL,
    accepted_at     TIMESTAMPTZ,
    declined_at     TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_team_invitations_pending
    ON team_invitations (tenant_id, email)
    WHERE status = 'pending';
CREATE INDEX idx_team_invitations_token ON team_invitations (invite_token);
CREATE INDEX idx_team_invitations_tenant ON team_invitations (tenant_id, status);

ALTER TABLE team_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_invitations FORCE ROW LEVEL SECURITY;
CREATE POLICY team_invitations_tenant ON team_invitations
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE TABLE user_permission_overrides (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    tenant_id   VARCHAR(255) NOT NULL,
    module      VARCHAR(50) NOT NULL,
    actions     CHAR(1)[] NOT NULL,
    source      VARCHAR(20) NOT NULL DEFAULT 'invite',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_tenant_module UNIQUE (user_id, tenant_id, module)
);

ALTER TABLE user_permission_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_permission_overrides FORCE ROW LEVEL SECURITY;
CREATE POLICY user_permission_overrides_tenant ON user_permission_overrides
    USING (tenant_id = current_setting('app.tenant_id', true));
