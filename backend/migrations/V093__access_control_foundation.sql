-- =============================================================================
-- V093: Access Control Foundation
-- =============================================================================
-- Purpose: Establish role-based access control (RBAC) with confidentiality levels
-- 
-- IRON LAWS COMPLIANCE:
-- - Law 12 (Audit Trail Immutability): All role changes are tracked via created_by,
--   assigned_by, and timestamps. Future audit_log integration will capture all mutations.
-- - Law 6 (Source Traceability): Permission checks are traceable via role_id -> 
--   role_permissions -> module + actions mapping.
--
-- Schema Components:
-- 1. confidentiality_level ENUM - Field Confidentiality Levels (L1-L5)
-- 2. roles - Role definitions with hierarchy
-- 3. role_permissions - Module-level CRUD+VAP permissions per role
-- 4. role_visibility - FCL visibility mapping per role
-- 5. user_tenant_roles - User-to-role assignment per tenant
-- =============================================================================

-- =============================================================================
-- SECTION 1: ENUM TYPE
-- =============================================================================

-- Field Confidentiality Levels (FCL)
-- L1: Public info (names, addresses, quantities)
-- L2: Internal operational (costs, margins, aging)
-- L3: Sensitive financial (detailed ledgers, audit trails)
-- L4: Restricted personal (salaries, tax IDs, bank details)
-- L5: Executive only (company financials, ownership)
CREATE TYPE confidentiality_level AS ENUM ('L1', 'L2', 'L3', 'L4', 'L5');

-- =============================================================================
-- SECTION 2: ROLES TABLE
-- =============================================================================

CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    code VARCHAR(50) NOT NULL,              -- OWNER, FINANCE_MGR, ACCOUNTANT, etc
    name VARCHAR(100) NOT NULL,
    description TEXT,
    parent_role_id UUID REFERENCES roles(id),
    hierarchy_level INT DEFAULT 0,          -- 0 = highest (owner), higher = lower rank
    is_system BOOLEAN DEFAULT FALSE,        -- system roles cannot be deleted
    is_active BOOLEAN DEFAULT TRUE,
    approval_limit BIGINT DEFAULT 0,        -- NULL = unlimited, 0 = no approval, >0 = limit in smallest currency unit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,                        -- IRON LAW 12: track who created
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, code)
);

-- Index for tenant lookups
CREATE INDEX idx_roles_tenant_id ON roles(tenant_id);
CREATE INDEX idx_roles_parent_id ON roles(parent_role_id);

COMMENT ON TABLE roles IS 'Role definitions with hierarchical structure and approval limits';
COMMENT ON COLUMN roles.hierarchy_level IS '0 = highest authority (owner), higher numbers = lower authority';
COMMENT ON COLUMN roles.approval_limit IS 'NULL = unlimited, 0 = no approval authority, >0 = max approval in smallest currency unit';
COMMENT ON COLUMN roles.is_system IS 'System roles (tenant_id=__SYSTEM__) are templates that cannot be deleted';

-- =============================================================================
-- SECTION 3: ROLE PERMISSIONS TABLE
-- =============================================================================

CREATE TABLE role_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    module VARCHAR(50) NOT NULL,            -- Module identifier (e.g., INVOICE, BILL, JOURNAL)
    actions CHAR(1)[] NOT NULL DEFAULT '{}', -- Permission flags: C=Create, R=Read, U=Update, D=Delete, V=Void, A=Approve, P=Print, E=Export
    max_confidentiality confidentiality_level, -- Maximum FCL this role can access for this module
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(role_id, module)
);

-- Index for permission lookups
CREATE INDEX idx_role_permissions_role_id ON role_permissions(role_id);
CREATE INDEX idx_role_permissions_module ON role_permissions(module);

COMMENT ON TABLE role_permissions IS 'Module-level permissions per role with confidentiality ceiling';
COMMENT ON COLUMN role_permissions.actions IS 'Array of permission flags: C=Create, R=Read, U=Update, D=Delete, V=Void, A=Approve, P=Print, E=Export';
COMMENT ON COLUMN role_permissions.max_confidentiality IS 'Maximum Field Confidentiality Level accessible for this module';

-- =============================================================================
-- SECTION 4: ROLE VISIBILITY TABLE (FCL Mapping)
-- =============================================================================

CREATE TABLE role_visibility (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    level confidentiality_level NOT NULL,   -- Which FCL levels this role can see
    UNIQUE(role_id, level)
);

-- Index for visibility lookups
CREATE INDEX idx_role_visibility_role_id ON role_visibility(role_id);

COMMENT ON TABLE role_visibility IS 'Defines which Field Confidentiality Levels each role can access';

-- =============================================================================
-- SECTION 5: USER TENANT ROLES TABLE
-- =============================================================================

CREATE TABLE user_tenant_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,                  -- References User table (external reference)
    tenant_id TEXT NOT NULL,
    role_id UUID NOT NULL REFERENCES roles(id),
    is_primary BOOLEAN DEFAULT FALSE,       -- Primary role for this tenant
    assigned_at TIMESTAMPTZ DEFAULT NOW(),  -- IRON LAW 12: when assigned
    assigned_by UUID,                       -- IRON LAW 12: who assigned
    UNIQUE(user_id, tenant_id, role_id)
);

-- Indexes for user and tenant lookups
CREATE INDEX idx_user_tenant_roles_user_id ON user_tenant_roles(user_id);
CREATE INDEX idx_user_tenant_roles_tenant_id ON user_tenant_roles(tenant_id);
CREATE INDEX idx_user_tenant_roles_role_id ON user_tenant_roles(role_id);

COMMENT ON TABLE user_tenant_roles IS 'Maps users to roles within specific tenants';
COMMENT ON COLUMN user_tenant_roles.is_primary IS 'Primary role is used as default when user has multiple roles in a tenant';
COMMENT ON COLUMN user_tenant_roles.assigned_by IS 'IRON LAW 12: Tracks who assigned this role for audit trail';

-- =============================================================================
-- SECTION 6: SEED SYSTEM ROLES
-- =============================================================================

-- System roles are templates with tenant_id = '__SYSTEM__'
-- These are copied to each tenant when they are created

INSERT INTO roles (tenant_id, code, name, description, hierarchy_level, is_system, approval_limit) VALUES
    ('__SYSTEM__', 'OWNER', 'Owner', 'Full access to all features and data. Can manage users and roles.', 0, TRUE, NULL),
    ('__SYSTEM__', 'FINANCE_MGR', 'Finance Manager', 'Manages financial operations. Can approve transactions up to limit.', 1, TRUE, 100000000),
    ('__SYSTEM__', 'ACCOUNTANT', 'Accountant', 'Handles day-to-day accounting. Read/write access to journals and reports.', 2, TRUE, 0),
    ('__SYSTEM__', 'CASHIER', 'Cashier', 'Manages cash transactions and receipts. Limited approval authority.', 2, TRUE, 10000000),
    ('__SYSTEM__', 'SALES', 'Sales', 'Manages sales orders and invoices. No approval authority.', 1, TRUE, 0),
    ('__SYSTEM__', 'PURCHASING', 'Purchasing', 'Manages purchase orders and bills. No approval authority.', 1, TRUE, 0),
    ('__SYSTEM__', 'HR_PAYROLL', 'HR & Payroll', 'Manages employee data and payroll. Access to personal data.', 1, TRUE, 0),
    ('__SYSTEM__', 'VIEWER', 'Viewer', 'Read-only access to basic operational data.', 2, TRUE, 0);

-- =============================================================================
-- SECTION 7: SEED ROLE PERMISSIONS
-- =============================================================================

-- Helper: Get system role ID by code
-- Permission flags: C=Create, R=Read, U=Update, D=Delete, V=Void, A=Approve, P=Print, E=Export

-- OWNER: Full permissions on all modules
INSERT INTO role_permissions (role_id, module, actions, max_confidentiality)
SELECT r.id, m.module, ARRAY['C','R','U','D','V','A','P','E']::CHAR(1)[], 'L5'::confidentiality_level
FROM roles r
CROSS JOIN (VALUES 
    ('INVOICE'), ('BILL'), ('JOURNAL'), ('PAYMENT'), ('RECEIPT'),
    ('CUSTOMER'), ('VENDOR'), ('PRODUCT'), ('ACCOUNT'), ('BANK'),
    ('REPORT'), ('SETTINGS'), ('USER_MANAGEMENT'), ('PAYROLL')
) AS m(module)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'OWNER';

-- FINANCE_MGR: Full CRUD + Approve on financial modules
INSERT INTO role_permissions (role_id, module, actions, max_confidentiality)
SELECT r.id, m.module, m.actions::CHAR(1)[], m.fcl::confidentiality_level
FROM roles r
CROSS JOIN (VALUES 
    ('INVOICE', ARRAY['C','R','U','D','V','A','P','E'], 'L3'),
    ('BILL', ARRAY['C','R','U','D','V','A','P','E'], 'L3'),
    ('JOURNAL', ARRAY['C','R','U','D','V','A','P','E'], 'L3'),
    ('PAYMENT', ARRAY['C','R','U','D','V','A','P','E'], 'L3'),
    ('RECEIPT', ARRAY['C','R','U','D','V','A','P','E'], 'L3'),
    ('CUSTOMER', ARRAY['C','R','U','D','P','E'], 'L3'),
    ('VENDOR', ARRAY['C','R','U','D','P','E'], 'L3'),
    ('PRODUCT', ARRAY['C','R','U','D','P','E'], 'L2'),
    ('ACCOUNT', ARRAY['C','R','U','D','P','E'], 'L3'),
    ('BANK', ARRAY['C','R','U','D','P','E'], 'L3'),
    ('REPORT', ARRAY['R','P','E'], 'L3'),
    ('SETTINGS', ARRAY['R','U'], 'L2')
) AS m(module, actions, fcl)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'FINANCE_MGR';

-- ACCOUNTANT: CRUD on accounting, no approval
INSERT INTO role_permissions (role_id, module, actions, max_confidentiality)
SELECT r.id, m.module, m.actions::CHAR(1)[], m.fcl::confidentiality_level
FROM roles r
CROSS JOIN (VALUES 
    ('INVOICE', ARRAY['C','R','U','P','E'], 'L3'),
    ('BILL', ARRAY['C','R','U','P','E'], 'L3'),
    ('JOURNAL', ARRAY['C','R','U','P','E'], 'L3'),
    ('PAYMENT', ARRAY['C','R','U','P','E'], 'L3'),
    ('RECEIPT', ARRAY['C','R','U','P','E'], 'L3'),
    ('CUSTOMER', ARRAY['R','U','P'], 'L2'),
    ('VENDOR', ARRAY['R','U','P'], 'L2'),
    ('PRODUCT', ARRAY['R','P'], 'L2'),
    ('ACCOUNT', ARRAY['R','U','P','E'], 'L3'),
    ('BANK', ARRAY['R','P','E'], 'L3'),
    ('REPORT', ARRAY['R','P','E'], 'L3')
) AS m(module, actions, fcl)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'ACCOUNTANT';

-- CASHIER: Limited to cash operations
INSERT INTO role_permissions (role_id, module, actions, max_confidentiality)
SELECT r.id, m.module, m.actions::CHAR(1)[], m.fcl::confidentiality_level
FROM roles r
CROSS JOIN (VALUES 
    ('INVOICE', ARRAY['R','P'], 'L2'),
    ('PAYMENT', ARRAY['C','R','U','A','P'], 'L2'),
    ('RECEIPT', ARRAY['C','R','U','A','P'], 'L2'),
    ('CUSTOMER', ARRAY['R'], 'L1'),
    ('BANK', ARRAY['R'], 'L2'),
    ('REPORT', ARRAY['R','P'], 'L2')
) AS m(module, actions, fcl)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'CASHIER';

-- SALES: Focus on sales operations
INSERT INTO role_permissions (role_id, module, actions, max_confidentiality)
SELECT r.id, m.module, m.actions::CHAR(1)[], m.fcl::confidentiality_level
FROM roles r
CROSS JOIN (VALUES 
    ('INVOICE', ARRAY['C','R','U','P','E'], 'L2'),
    ('RECEIPT', ARRAY['C','R','U','P'], 'L2'),
    ('CUSTOMER', ARRAY['C','R','U','P'], 'L2'),
    ('PRODUCT', ARRAY['R','P'], 'L2'),
    ('REPORT', ARRAY['R','P'], 'L2')
) AS m(module, actions, fcl)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'SALES';

-- PURCHASING: Focus on purchasing operations
INSERT INTO role_permissions (role_id, module, actions, max_confidentiality)
SELECT r.id, m.module, m.actions::CHAR(1)[], m.fcl::confidentiality_level
FROM roles r
CROSS JOIN (VALUES 
    ('BILL', ARRAY['C','R','U','P','E'], 'L2'),
    ('PAYMENT', ARRAY['C','R','U','P'], 'L2'),
    ('VENDOR', ARRAY['C','R','U','P'], 'L2'),
    ('PRODUCT', ARRAY['R','U','P'], 'L2'),
    ('REPORT', ARRAY['R','P'], 'L2')
) AS m(module, actions, fcl)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'PURCHASING';

-- HR_PAYROLL: Focus on employee and payroll data
INSERT INTO role_permissions (role_id, module, actions, max_confidentiality)
SELECT r.id, m.module, m.actions::CHAR(1)[], m.fcl::confidentiality_level
FROM roles r
CROSS JOIN (VALUES 
    ('PAYROLL', ARRAY['C','R','U','D','A','P','E'], 'L4'),
    ('JOURNAL', ARRAY['R','P'], 'L2'),
    ('PAYMENT', ARRAY['C','R','P'], 'L2'),
    ('REPORT', ARRAY['R','P'], 'L4'),
    ('SETTINGS', ARRAY['R'], 'L2')
) AS m(module, actions, fcl)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'HR_PAYROLL';

-- VIEWER: Read-only access
INSERT INTO role_permissions (role_id, module, actions, max_confidentiality)
SELECT r.id, m.module, ARRAY['R']::CHAR(1)[], 'L1'::confidentiality_level
FROM roles r
CROSS JOIN (VALUES 
    ('INVOICE'), ('BILL'), ('CUSTOMER'), ('VENDOR'), 
    ('PRODUCT'), ('REPORT')
) AS m(module)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'VIEWER';

-- =============================================================================
-- SECTION 8: SEED ROLE VISIBILITY (FCL Access)
-- =============================================================================

-- OWNER: Access to all confidentiality levels (L1-L5)
INSERT INTO role_visibility (role_id, level)
SELECT r.id, l.level::confidentiality_level
FROM roles r
CROSS JOIN (VALUES ('L1'), ('L2'), ('L3'), ('L4'), ('L5')) AS l(level)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'OWNER';

-- FINANCE_MGR: Access to L1, L2, L3
INSERT INTO role_visibility (role_id, level)
SELECT r.id, l.level::confidentiality_level
FROM roles r
CROSS JOIN (VALUES ('L1'), ('L2'), ('L3')) AS l(level)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'FINANCE_MGR';

-- ACCOUNTANT: Access to L1, L2, L3
INSERT INTO role_visibility (role_id, level)
SELECT r.id, l.level::confidentiality_level
FROM roles r
CROSS JOIN (VALUES ('L1'), ('L2'), ('L3')) AS l(level)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'ACCOUNTANT';

-- CASHIER: Access to L1, L2
INSERT INTO role_visibility (role_id, level)
SELECT r.id, l.level::confidentiality_level
FROM roles r
CROSS JOIN (VALUES ('L1'), ('L2')) AS l(level)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'CASHIER';

-- SALES: Access to L1, L2
INSERT INTO role_visibility (role_id, level)
SELECT r.id, l.level::confidentiality_level
FROM roles r
CROSS JOIN (VALUES ('L1'), ('L2')) AS l(level)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'SALES';

-- PURCHASING: Access to L1, L2
INSERT INTO role_visibility (role_id, level)
SELECT r.id, l.level::confidentiality_level
FROM roles r
CROSS JOIN (VALUES ('L1'), ('L2')) AS l(level)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'PURCHASING';

-- HR_PAYROLL: Access to L1, L2, L4 (skips L3, has L4 for personal data)
INSERT INTO role_visibility (role_id, level)
SELECT r.id, l.level::confidentiality_level
FROM roles r
CROSS JOIN (VALUES ('L1'), ('L2'), ('L4')) AS l(level)
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'HR_PAYROLL';

-- VIEWER: Access to L1 only
INSERT INTO role_visibility (role_id, level)
SELECT r.id, 'L1'::confidentiality_level
FROM roles r
WHERE r.tenant_id = '__SYSTEM__' AND r.code = 'VIEWER';

-- =============================================================================
-- SECTION 9: HELPER FUNCTIONS FOR PERMISSION CHECKS
-- =============================================================================

-- Function to check if a user has a specific permission on a module
-- IRON LAW 6: Source traceability - returns role_id for audit logging
CREATE OR REPLACE FUNCTION check_user_permission(
    p_user_id UUID,
    p_tenant_id TEXT,
    p_module VARCHAR(50),
    p_action CHAR(1)
) RETURNS TABLE (
    has_permission BOOLEAN,
    role_id UUID,
    role_code VARCHAR(50)
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        TRUE AS has_permission,
        r.id AS role_id,
        r.code AS role_code
    FROM user_tenant_roles utr
    JOIN roles r ON r.id = utr.role_id
    JOIN role_permissions rp ON rp.role_id = r.id
    WHERE utr.user_id = p_user_id
      AND utr.tenant_id = p_tenant_id
      AND rp.module = p_module
      AND p_action = ANY(rp.actions)
      AND r.is_active = TRUE
    LIMIT 1;
    
    -- Return false if no permission found
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, NULL::UUID, NULL::VARCHAR(50);
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Function to check if a user can view a specific confidentiality level
CREATE OR REPLACE FUNCTION check_user_visibility(
    p_user_id UUID,
    p_tenant_id TEXT,
    p_level confidentiality_level
) RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1
        FROM user_tenant_roles utr
        JOIN roles r ON r.id = utr.role_id
        JOIN role_visibility rv ON rv.role_id = r.id
        WHERE utr.user_id = p_user_id
          AND utr.tenant_id = p_tenant_id
          AND rv.level = p_level
          AND r.is_active = TRUE
    );
END;
$$ LANGUAGE plpgsql;

-- Function to get user's maximum approval limit for a tenant
CREATE OR REPLACE FUNCTION get_user_approval_limit(
    p_user_id UUID,
    p_tenant_id TEXT
) RETURNS BIGINT AS $$
DECLARE
    v_limit BIGINT;
BEGIN
    SELECT MAX(COALESCE(r.approval_limit, 9223372036854775807)) -- Max BIGINT if NULL (unlimited)
    INTO v_limit
    FROM user_tenant_roles utr
    JOIN roles r ON r.id = utr.role_id
    WHERE utr.user_id = p_user_id
      AND utr.tenant_id = p_tenant_id
      AND r.is_active = TRUE;
    
    RETURN COALESCE(v_limit, 0);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION check_user_permission IS 'IRON LAW 6: Returns role_id for audit trail when checking permissions';
COMMENT ON FUNCTION check_user_visibility IS 'Check if user can access a specific Field Confidentiality Level';
COMMENT ON FUNCTION get_user_approval_limit IS 'Get maximum approval limit across all user roles in a tenant';

-- =============================================================================
-- END OF MIGRATION
-- =============================================================================
