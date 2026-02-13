-- =============================================================
-- V056: Policy Engine Tables
-- =============================================================
-- IRON LAW Compliant:
-- - Law 0: Separation of Concerns - Policy tables separate from business
-- - Law 10: AI Safety Boundary - AI flags for gatekeeping
-- - Law 12: Audit Immutability - Append-only audit log

-- =============================================================
-- Tenant Roles Table
-- =============================================================
CREATE TABLE IF NOT EXISTS tenant_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    hierarchy_level INTEGER NOT NULL DEFAULT 10,
    is_system_role BOOLEAN DEFAULT FALSE,
    permissions_json JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT uq_tenant_role_code UNIQUE (tenant_id, code)
);

CREATE INDEX IF NOT EXISTS idx_tenant_roles_tenant ON tenant_roles(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_roles_code ON tenant_roles(tenant_id, code);

-- =============================================================
-- Tenant Members Table (User-Tenant-Role mapping)
-- =============================================================
CREATE TABLE IF NOT EXISTS tenant_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    role_id UUID NOT NULL REFERENCES tenant_roles(id) ON DELETE CASCADE,
    is_active BOOLEAN DEFAULT TRUE,
    joined_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT uq_tenant_member UNIQUE (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_members_tenant ON tenant_members(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_members_user ON tenant_members(user_id);
CREATE INDEX IF NOT EXISTS idx_tenant_members_role ON tenant_members(role_id);

-- =============================================================
-- Role Permissions Table
-- =============================================================
CREATE TABLE IF NOT EXISTS role_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id UUID NOT NULL REFERENCES tenant_roles(id) ON DELETE CASCADE,
    module VARCHAR(50) NOT NULL,
    actions TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    max_confidentiality VARCHAR(10) DEFAULT 'L1',
    entity_types TEXT[],
    restrictions JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT uq_role_module_permission UNIQUE (role_id, module)
);

CREATE INDEX IF NOT EXISTS idx_role_permissions_role ON role_permissions(role_id);
CREATE INDEX IF NOT EXISTS idx_role_permissions_module ON role_permissions(role_id, module);

-- =============================================================
-- Role Visibility Table (FCL - Financial Confidentiality Level)
-- =============================================================
CREATE TABLE IF NOT EXISTS role_visibility (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id UUID NOT NULL REFERENCES tenant_roles(id) ON DELETE CASCADE,
    level VARCHAR(10) NOT NULL,
    allowed_modules TEXT[],
    excluded_fields TEXT[],
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_role_visibility_role ON role_visibility(role_id);

-- =============================================================
-- Approval Workflows Table
-- =============================================================
CREATE TABLE IF NOT EXISTS approval_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    document_type VARCHAR(50) NOT NULL,
    min_amount DECIMAL(20,2),
    max_amount DECIMAL(20,2),
    min_approvals INTEGER NOT NULL DEFAULT 1,
    approver_role_codes TEXT[] NOT NULL,
    require_sequential BOOLEAN DEFAULT FALSE,
    expiry_hours INTEGER DEFAULT 72,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_approval_workflows_tenant ON approval_workflows(tenant_id);
CREATE INDEX IF NOT EXISTS idx_approval_workflows_document ON approval_workflows(tenant_id, document_type);
CREATE INDEX IF NOT EXISTS idx_approval_workflows_amount ON approval_workflows(tenant_id, document_type, min_amount, max_amount);

-- =============================================================
-- Policy Audit Log Table (IRON LAW 12: Immutable)
-- =============================================================
CREATE TABLE IF NOT EXISTS policy_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    user_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    role_code VARCHAR(50),
    is_ai_agent BOOLEAN DEFAULT FALSE,
    action VARCHAR(10) NOT NULL,
    resource_module VARCHAR(50) NOT NULL,
    resource_entity_type VARCHAR(50),
    resource_entity_id UUID,
    allowed BOOLEAN NOT NULL,
    reason VARCHAR(100) NOT NULL,
    request_id UUID,
    ip_address VARCHAR(45),
    user_agent TEXT,
    metadata JSONB
);

-- Partitioning by month for performance (optional, depends on volume)
-- For now, create indexes for common queries

CREATE INDEX IF NOT EXISTS idx_policy_audit_timestamp ON policy_audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_policy_audit_user ON policy_audit_log(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_policy_audit_tenant ON policy_audit_log(tenant_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_policy_audit_denied ON policy_audit_log(tenant_id, allowed, timestamp DESC) WHERE allowed = FALSE;
CREATE INDEX IF NOT EXISTS idx_policy_audit_ai ON policy_audit_log(tenant_id, is_ai_agent, timestamp DESC) WHERE is_ai_agent = TRUE;

-- =============================================================
-- Immutable Transactions Table (for tracking locked records)
-- =============================================================
CREATE TABLE IF NOT EXISTS immutable_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id UUID NOT NULL,
    locked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    locked_by UUID NOT NULL,
    reason VARCHAR(200),
    
    CONSTRAINT uq_immutable_entity UNIQUE (tenant_id, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_immutable_entity ON immutable_transactions(tenant_id, entity_type, entity_id);

-- =============================================================
-- Seed Default Roles
-- =============================================================
-- Note: These are templates, actual roles should be created per tenant

-- Example trigger for updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_tenant_roles_updated_at BEFORE UPDATE ON tenant_roles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_approval_workflows_updated_at BEFORE UPDATE ON approval_workflows
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =============================================================
-- Comments for documentation
-- =============================================================
COMMENT ON TABLE tenant_roles IS 'Role definitions per tenant for RBAC';
COMMENT ON TABLE tenant_members IS 'User membership in tenants with role assignment';
COMMENT ON TABLE role_permissions IS 'Permission grants per role per module';
COMMENT ON TABLE role_visibility IS 'FCL (Financial Confidentiality Level) per role';
COMMENT ON TABLE approval_workflows IS 'Approval workflow configuration per document type';
COMMENT ON TABLE policy_audit_log IS 'Immutable audit log for all policy decisions (IRON LAW 12)';
COMMENT ON TABLE immutable_transactions IS 'Registry of locked/immutable transactions';

COMMENT ON COLUMN role_permissions.actions IS 'Array of action codes: C=Create, R=Read, U=Update, D=Delete, V=Void, A=Approve, P=Post, E=Export';
COMMENT ON COLUMN role_visibility.level IS 'Confidentiality level: L1=Public, L2=Internal, L3=Confidential, L4=Restricted, L5=TopSecret';
COMMENT ON COLUMN policy_audit_log.is_ai_agent IS 'Flag for AI-initiated requests (IRON LAW 10: AI Safety Boundary)';
