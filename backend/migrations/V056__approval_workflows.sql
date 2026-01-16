-- =============================================
-- V056: Approval Workflows (Alur Persetujuan)
-- Purpose: Multi-level approval for transactions based on amount or document type
-- NO JOURNAL ENTRY - This is a process control system
-- =============================================

-- ============================================================================
-- APPROVAL WORKFLOW TEMPLATES
-- ============================================================================
CREATE TABLE approval_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    name VARCHAR(100) NOT NULL,
    description TEXT,

    -- Document type this workflow applies to
    document_type VARCHAR(50) NOT NULL, -- purchase_order, bill, expense, sales_order, etc

    -- Conditions (when to trigger)
    min_amount BIGINT DEFAULT 0, -- trigger if amount >= this
    max_amount BIGINT, -- trigger if amount <= this (NULL = no max)

    -- Settings
    is_active BOOLEAN DEFAULT true,
    is_sequential BOOLEAN DEFAULT true, -- must approve in order vs parallel
    auto_approve_below_min BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_approval_workflows UNIQUE(tenant_id, name)
);

-- ============================================================================
-- APPROVAL LEVELS (STEPS)
-- ============================================================================
CREATE TABLE approval_levels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID NOT NULL REFERENCES approval_workflows(id) ON DELETE CASCADE,

    level_order INTEGER NOT NULL, -- 1, 2, 3...
    name VARCHAR(100) NOT NULL, -- "Manager", "Director", "Finance"

    -- Who can approve at this level
    approver_type VARCHAR(20) NOT NULL, -- user, role, any_of_users, any_of_roles
    approver_user_id UUID, -- specific user (if approver_type = 'user')
    approver_role VARCHAR(50), -- role name (if approver_type = 'role')
    approver_user_ids UUID[], -- list of users (if approver_type = 'any_of_users')
    approver_roles VARCHAR(50)[], -- list of roles (if approver_type = 'any_of_roles')

    -- Escalation
    auto_escalate_hours INTEGER, -- auto-escalate after X hours (NULL = no escalation)
    escalate_to_user_id UUID,

    -- Can this level reject?
    can_reject BOOLEAN DEFAULT true,

    -- Notification
    notify_on_pending BOOLEAN DEFAULT true,
    notify_on_approved BOOLEAN DEFAULT true,
    notify_on_rejected BOOLEAN DEFAULT true,

    CONSTRAINT uq_approval_levels UNIQUE(workflow_id, level_order),
    CONSTRAINT chk_approver_type CHECK (approver_type IN ('user', 'role', 'any_of_users', 'any_of_roles'))
);

-- ============================================================================
-- APPROVAL REQUESTS (INSTANCES)
-- ============================================================================
CREATE TABLE approval_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    workflow_id UUID NOT NULL REFERENCES approval_workflows(id),

    -- Document reference
    document_type VARCHAR(50) NOT NULL,
    document_id UUID NOT NULL,
    document_number VARCHAR(100),
    document_amount BIGINT,

    -- Requester
    requested_by UUID NOT NULL,
    requested_at TIMESTAMPTZ DEFAULT NOW(),

    -- Current status
    current_level INTEGER DEFAULT 1,
    status VARCHAR(20) DEFAULT 'pending', -- pending, approved, rejected, cancelled

    -- Completion
    completed_at TIMESTAMPTZ,

    -- Notes
    notes TEXT,

    CONSTRAINT uq_approval_requests UNIQUE(tenant_id, document_type, document_id),
    CONSTRAINT chk_approval_status CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled'))
);

-- ============================================================================
-- APPROVAL ACTIONS (HISTORY PER LEVEL)
-- ============================================================================
CREATE TABLE approval_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
    level_id UUID NOT NULL REFERENCES approval_levels(id),

    -- Action
    action VARCHAR(20) NOT NULL, -- approved, rejected, escalated, skipped
    action_by UUID NOT NULL,
    action_at TIMESTAMPTZ DEFAULT NOW(),

    -- Comments
    comments TEXT,

    -- If escalated
    escalated_to UUID,
    escalation_reason TEXT,

    CONSTRAINT chk_action_type CHECK (action IN ('approved', 'rejected', 'escalated', 'skipped'))
);

-- ============================================================================
-- APPROVAL DELEGATES (VACATION/TEMPORARY)
-- ============================================================================
CREATE TABLE approval_delegates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Original approver
    approver_user_id UUID NOT NULL,

    -- Delegate
    delegate_user_id UUID NOT NULL,

    -- Period
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,

    -- Scope
    workflow_ids UUID[], -- NULL = all workflows

    -- Status
    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT chk_delegate_dates CHECK (end_date >= start_date)
);

-- ============================================================================
-- ADD APPROVAL COLUMNS TO TRANSACTION TABLES
-- ============================================================================

-- Purchase Orders
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS approval_status VARCHAR(20) DEFAULT 'not_required';
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS approval_request_id UUID REFERENCES approval_requests(id);

-- Bills
ALTER TABLE bills ADD COLUMN IF NOT EXISTS approval_status VARCHAR(20) DEFAULT 'not_required';
ALTER TABLE bills ADD COLUMN IF NOT EXISTS approval_request_id UUID REFERENCES approval_requests(id);

-- Sales Orders
ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS approval_status VARCHAR(20) DEFAULT 'not_required';
ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS approval_request_id UUID REFERENCES approval_requests(id);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE approval_workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_levels ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_delegates ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_approval_workflows ON approval_workflows
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_approval_levels ON approval_levels
    USING (workflow_id IN (SELECT id FROM approval_workflows WHERE tenant_id = current_setting('app.tenant_id', true)));
CREATE POLICY rls_approval_requests ON approval_requests
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_approval_actions ON approval_actions
    USING (request_id IN (SELECT id FROM approval_requests WHERE tenant_id = current_setting('app.tenant_id', true)));
CREATE POLICY rls_approval_delegates ON approval_delegates
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX idx_approval_workflows_tenant ON approval_workflows(tenant_id);
CREATE INDEX idx_approval_workflows_doctype ON approval_workflows(tenant_id, document_type) WHERE is_active = true;
CREATE INDEX idx_approval_levels_workflow ON approval_levels(workflow_id);
CREATE INDEX idx_approval_requests_status ON approval_requests(tenant_id, status) WHERE status = 'pending';
CREATE INDEX idx_approval_requests_document ON approval_requests(document_type, document_id);
CREATE INDEX idx_approval_requests_requester ON approval_requests(requested_by, status);
CREATE INDEX idx_approval_actions_request ON approval_actions(request_id);
CREATE INDEX idx_approval_delegates_approver ON approval_delegates(approver_user_id) WHERE is_active = true;
CREATE INDEX idx_approval_delegates_period ON approval_delegates(start_date, end_date) WHERE is_active = true;

-- ============================================================================
-- FUNCTION: GET APPLICABLE WORKFLOW
-- ============================================================================
CREATE OR REPLACE FUNCTION get_applicable_workflow(
    p_tenant_id TEXT,
    p_document_type VARCHAR(50),
    p_amount BIGINT
) RETURNS UUID AS $$
DECLARE
    v_workflow_id UUID;
BEGIN
    SELECT id INTO v_workflow_id
    FROM approval_workflows
    WHERE tenant_id = p_tenant_id
    AND document_type = p_document_type
    AND is_active = true
    AND p_amount >= min_amount
    AND (max_amount IS NULL OR p_amount <= max_amount)
    ORDER BY min_amount DESC
    LIMIT 1;

    RETURN v_workflow_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- FUNCTION: CHECK IF USER CAN APPROVE
-- ============================================================================
CREATE OR REPLACE FUNCTION can_user_approve(
    p_level_id UUID,
    p_user_id UUID,
    p_user_role VARCHAR(50)
) RETURNS BOOLEAN AS $$
DECLARE
    v_level approval_levels%ROWTYPE;
    v_delegate_user_id UUID;
    v_workflow_id UUID;
BEGIN
    SELECT * INTO v_level FROM approval_levels WHERE id = p_level_id;

    IF v_level.id IS NULL THEN
        RETURN FALSE;
    END IF;

    -- Check direct approval
    IF v_level.approver_type = 'user' AND v_level.approver_user_id = p_user_id THEN
        RETURN TRUE;
    END IF;

    IF v_level.approver_type = 'role' AND v_level.approver_role = p_user_role THEN
        RETURN TRUE;
    END IF;

    IF v_level.approver_type = 'any_of_users' AND p_user_id = ANY(v_level.approver_user_ids) THEN
        RETURN TRUE;
    END IF;

    IF v_level.approver_type = 'any_of_roles' AND p_user_role = ANY(v_level.approver_roles) THEN
        RETURN TRUE;
    END IF;

    -- Check delegation (if approver_type is 'user')
    IF v_level.approver_type = 'user' THEN
        SELECT delegate_user_id INTO v_delegate_user_id
        FROM approval_delegates
        WHERE approver_user_id = v_level.approver_user_id
        AND delegate_user_id = p_user_id
        AND CURRENT_DATE BETWEEN start_date AND end_date
        AND is_active = true
        AND (workflow_ids IS NULL OR v_level.workflow_id = ANY(workflow_ids))
        LIMIT 1;

        IF v_delegate_user_id IS NOT NULL THEN
            RETURN TRUE;
        END IF;
    END IF;

    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- FUNCTION: GET PENDING APPROVALS FOR USER
-- ============================================================================
CREATE OR REPLACE FUNCTION get_pending_approvals_for_user(
    p_tenant_id TEXT,
    p_user_id UUID,
    p_user_role VARCHAR(50)
) RETURNS TABLE (
    request_id UUID,
    workflow_name VARCHAR(100),
    document_type VARCHAR(50),
    document_id UUID,
    document_number VARCHAR(100),
    document_amount BIGINT,
    current_level INTEGER,
    level_name VARCHAR(100),
    requested_by UUID,
    requested_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ar.id as request_id,
        aw.name as workflow_name,
        ar.document_type,
        ar.document_id,
        ar.document_number,
        ar.document_amount,
        ar.current_level,
        al.name as level_name,
        ar.requested_by,
        ar.requested_at
    FROM approval_requests ar
    JOIN approval_workflows aw ON ar.workflow_id = aw.id
    JOIN approval_levels al ON al.workflow_id = aw.id AND al.level_order = ar.current_level
    WHERE ar.tenant_id = p_tenant_id
    AND ar.status = 'pending'
    AND can_user_approve(al.id, p_user_id, p_user_role)
    ORDER BY ar.requested_at ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- FUNCTION: GET APPROVAL REQUEST DETAIL WITH HISTORY
-- ============================================================================
CREATE OR REPLACE FUNCTION get_approval_request_detail(p_request_id UUID)
RETURNS TABLE (
    request_id UUID,
    workflow_id UUID,
    workflow_name VARCHAR(100),
    document_type VARCHAR(50),
    document_id UUID,
    document_number VARCHAR(100),
    document_amount BIGINT,
    current_level INTEGER,
    status VARCHAR(20),
    requested_by UUID,
    requested_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    actions JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ar.id as request_id,
        ar.workflow_id,
        aw.name as workflow_name,
        ar.document_type,
        ar.document_id,
        ar.document_number,
        ar.document_amount,
        ar.current_level,
        ar.status,
        ar.requested_by,
        ar.requested_at,
        ar.completed_at,
        (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'level_order', al.level_order,
                    'level_name', al.name,
                    'action', aa.action,
                    'action_by', aa.action_by,
                    'action_at', aa.action_at,
                    'comments', aa.comments
                ) ORDER BY aa.action_at
            )
            FROM approval_actions aa
            JOIN approval_levels al ON aa.level_id = al.id
            WHERE aa.request_id = ar.id
        ) as actions
    FROM approval_requests ar
    JOIN approval_workflows aw ON ar.workflow_id = aw.id
    WHERE ar.id = p_request_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- FUNCTION: APPROVAL STATISTICS
-- ============================================================================
CREATE OR REPLACE FUNCTION get_approval_statistics(
    p_tenant_id TEXT,
    p_from_date DATE DEFAULT NULL,
    p_to_date DATE DEFAULT NULL
)
RETURNS TABLE (
    document_type VARCHAR(50),
    total_requests BIGINT,
    pending_count BIGINT,
    approved_count BIGINT,
    rejected_count BIGINT,
    cancelled_count BIGINT,
    avg_approval_hours NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ar.document_type,
        COUNT(*)::BIGINT as total_requests,
        COUNT(CASE WHEN ar.status = 'pending' THEN 1 END)::BIGINT as pending_count,
        COUNT(CASE WHEN ar.status = 'approved' THEN 1 END)::BIGINT as approved_count,
        COUNT(CASE WHEN ar.status = 'rejected' THEN 1 END)::BIGINT as rejected_count,
        COUNT(CASE WHEN ar.status = 'cancelled' THEN 1 END)::BIGINT as cancelled_count,
        ROUND(AVG(
            CASE WHEN ar.status = 'approved' AND ar.completed_at IS NOT NULL
            THEN EXTRACT(EPOCH FROM (ar.completed_at - ar.requested_at)) / 3600
            END
        )::NUMERIC, 2) as avg_approval_hours
    FROM approval_requests ar
    WHERE ar.tenant_id = p_tenant_id
    AND (p_from_date IS NULL OR ar.requested_at::DATE >= p_from_date)
    AND (p_to_date IS NULL OR ar.requested_at::DATE <= p_to_date)
    GROUP BY ar.document_type
    ORDER BY COUNT(*) DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- COMMENTS
-- ============================================================================
COMMENT ON TABLE approval_workflows IS 'Templates defining approval process for different document types and amounts';
COMMENT ON TABLE approval_levels IS 'Steps/levels within an approval workflow';
COMMENT ON TABLE approval_requests IS 'Active approval instances for documents';
COMMENT ON TABLE approval_actions IS 'History of approval/rejection actions per request';
COMMENT ON TABLE approval_delegates IS 'Temporary delegation of approval authority (vacation coverage)';
COMMENT ON FUNCTION get_applicable_workflow IS 'Find the best matching workflow for a document type and amount';
COMMENT ON FUNCTION can_user_approve IS 'Check if a user has authority to approve at a specific level';
