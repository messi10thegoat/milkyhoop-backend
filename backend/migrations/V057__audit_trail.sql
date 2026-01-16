-- =============================================
-- V057: Audit Trail (Jejak Audit)
-- Purpose: Log all activities and data changes for compliance and security
-- NO JOURNAL ENTRY - This is a logging system
-- =============================================

-- ============================================================================
-- AUDIT LOG TABLE
-- ============================================================================
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- When
    event_time TIMESTAMPTZ DEFAULT NOW(),

    -- Who
    user_id UUID,
    user_email VARCHAR(255),
    user_name VARCHAR(255),
    ip_address INET,
    user_agent TEXT,

    -- What
    action VARCHAR(50) NOT NULL, -- create, read, update, delete, login, logout, export, etc
    entity_type VARCHAR(100) NOT NULL, -- invoice, bill, journal_entry, user, etc
    entity_id UUID,
    entity_number VARCHAR(100), -- human-readable identifier

    -- Details
    description TEXT,

    -- Data changes (for update/delete)
    old_values JSONB,
    new_values JSONB,
    changed_fields TEXT[], -- list of changed field names

    -- Request context
    request_id UUID, -- correlation ID
    request_path TEXT,
    request_method VARCHAR(10),

    -- Categorization
    category VARCHAR(50), -- accounting, inventory, user_management, security, etc
    severity VARCHAR(20) DEFAULT 'info', -- info, warning, error, critical

    -- Indexing
    search_text TEXT -- for full-text search
);

-- ============================================================================
-- SENSITIVE DATA ACCESS LOG
-- ============================================================================
CREATE TABLE sensitive_data_access (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    access_time TIMESTAMPTZ DEFAULT NOW(),
    user_id UUID NOT NULL,

    -- What was accessed
    data_type VARCHAR(50) NOT NULL, -- salary, bank_account, customer_data, etc
    entity_type VARCHAR(100),
    entity_id UUID,

    -- Context
    reason TEXT,
    authorized_by UUID,

    -- Export tracking
    was_exported BOOLEAN DEFAULT false,
    export_format VARCHAR(20)
);

-- ============================================================================
-- LOGIN/SESSION TRACKING
-- ============================================================================
CREATE TABLE login_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT,

    user_id UUID NOT NULL,
    user_email VARCHAR(255),

    -- Session
    session_id UUID,
    login_time TIMESTAMPTZ DEFAULT NOW(),
    logout_time TIMESTAMPTZ,

    -- Context
    ip_address INET,
    user_agent TEXT,
    device_type VARCHAR(50),
    location_country VARCHAR(100),
    location_city VARCHAR(100),

    -- Status
    login_status VARCHAR(20), -- success, failed_password, failed_2fa, blocked
    failure_reason TEXT,

    -- Security
    is_suspicious BOOLEAN DEFAULT false,
    mfa_used BOOLEAN DEFAULT false
);

-- ============================================================================
-- DATA RETENTION POLICY
-- ============================================================================
CREATE TABLE audit_retention_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    category VARCHAR(50) NOT NULL,
    retention_days INTEGER NOT NULL DEFAULT 365,

    -- Actions
    archive_after_days INTEGER,
    delete_after_days INTEGER,

    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_retention_policy UNIQUE(tenant_id, category)
);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sensitive_data_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE login_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_retention_policies ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_audit_logs ON audit_logs
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_sensitive_data_access ON sensitive_data_access
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_login_history ON login_history
    USING (tenant_id = current_setting('app.tenant_id', true) OR tenant_id IS NULL);
CREATE POLICY rls_audit_retention_policies ON audit_retention_policies
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- INDEXES FOR COMMON QUERIES
-- ============================================================================
CREATE INDEX idx_audit_logs_time ON audit_logs(tenant_id, event_time DESC);
CREATE INDEX idx_audit_logs_user ON audit_logs(user_id, event_time DESC);
CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_logs_action ON audit_logs(tenant_id, action, event_time DESC);
CREATE INDEX idx_audit_logs_category ON audit_logs(tenant_id, category, event_time DESC);
CREATE INDEX idx_audit_logs_search ON audit_logs USING GIN(to_tsvector('english', search_text));

CREATE INDEX idx_sensitive_access_time ON sensitive_data_access(tenant_id, access_time DESC);
CREATE INDEX idx_sensitive_access_user ON sensitive_data_access(user_id, access_time DESC);

CREATE INDEX idx_login_history_user ON login_history(user_id, login_time DESC);
CREATE INDEX idx_login_history_status ON login_history(login_status) WHERE login_status != 'success';
CREATE INDEX idx_login_history_suspicious ON login_history(is_suspicious) WHERE is_suspicious = true;

-- ============================================================================
-- FUNCTION: LOG AUDIT EVENT
-- ============================================================================
CREATE OR REPLACE FUNCTION log_audit_event(
    p_tenant_id TEXT,
    p_user_id UUID,
    p_action VARCHAR(50),
    p_entity_type VARCHAR(100),
    p_entity_id UUID,
    p_entity_number VARCHAR(100),
    p_description TEXT,
    p_old_values JSONB DEFAULT NULL,
    p_new_values JSONB DEFAULT NULL,
    p_category VARCHAR(50) DEFAULT 'general',
    p_severity VARCHAR(20) DEFAULT 'info'
) RETURNS UUID AS $$
DECLARE
    v_audit_id UUID;
    v_changed_fields TEXT[];
    v_search_text TEXT;
BEGIN
    -- Calculate changed fields
    IF p_old_values IS NOT NULL AND p_new_values IS NOT NULL THEN
        SELECT ARRAY_AGG(key)
        INTO v_changed_fields
        FROM (
            SELECT key FROM jsonb_object_keys(p_old_values) AS key
            WHERE p_old_values->key IS DISTINCT FROM p_new_values->key
            UNION
            SELECT key FROM jsonb_object_keys(p_new_values) AS key
            WHERE NOT p_old_values ? key
        ) AS changes;
    END IF;

    -- Build search text
    v_search_text := COALESCE(p_entity_number, '') || ' ' ||
                     COALESCE(p_description, '') || ' ' ||
                     COALESCE(p_action, '');

    INSERT INTO audit_logs (
        tenant_id, user_id, action, entity_type, entity_id, entity_number,
        description, old_values, new_values, changed_fields,
        category, severity, search_text
    ) VALUES (
        p_tenant_id, p_user_id, p_action, p_entity_type, p_entity_id, p_entity_number,
        p_description, p_old_values, p_new_values, v_changed_fields,
        p_category, p_severity, v_search_text
    ) RETURNING id INTO v_audit_id;

    RETURN v_audit_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- GENERIC AUDIT TRIGGER FUNCTION
-- ============================================================================
CREATE OR REPLACE FUNCTION audit_trigger_func()
RETURNS TRIGGER AS $$
DECLARE
    v_old_values JSONB;
    v_new_values JSONB;
    v_action VARCHAR(50);
    v_entity_number VARCHAR(100);
    v_tenant_id TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_action := 'create';
        v_new_values := to_jsonb(NEW);
        v_tenant_id := NEW.tenant_id;

        -- Try to extract entity number from common columns
        v_entity_number := COALESCE(
            CASE WHEN TG_TABLE_NAME = 'sales_invoices' THEN NEW.invoice_number
                 WHEN TG_TABLE_NAME = 'bills' THEN NEW.bill_number
                 WHEN TG_TABLE_NAME = 'journal_entries' THEN NEW.journal_number
                 WHEN TG_TABLE_NAME = 'fixed_assets' THEN NEW.asset_number
                 WHEN TG_TABLE_NAME = 'vendor_deposits' THEN NEW.deposit_number
                 WHEN TG_TABLE_NAME = 'customer_deposits' THEN NEW.deposit_number
                 WHEN TG_TABLE_NAME = 'cheques' THEN NEW.cheque_number
                 ELSE NULL
            END,
            NEW.id::TEXT
        );

        PERFORM log_audit_event(
            v_tenant_id, NULL, v_action, TG_TABLE_NAME, NEW.id,
            v_entity_number, 'Created ' || TG_TABLE_NAME,
            NULL, v_new_values, 'data_change', 'info'
        );
        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN
        v_action := 'update';
        v_old_values := to_jsonb(OLD);
        v_new_values := to_jsonb(NEW);
        v_tenant_id := NEW.tenant_id;

        v_entity_number := COALESCE(
            CASE WHEN TG_TABLE_NAME = 'sales_invoices' THEN NEW.invoice_number
                 WHEN TG_TABLE_NAME = 'bills' THEN NEW.bill_number
                 WHEN TG_TABLE_NAME = 'journal_entries' THEN NEW.journal_number
                 WHEN TG_TABLE_NAME = 'fixed_assets' THEN NEW.asset_number
                 WHEN TG_TABLE_NAME = 'vendor_deposits' THEN NEW.deposit_number
                 WHEN TG_TABLE_NAME = 'customer_deposits' THEN NEW.deposit_number
                 WHEN TG_TABLE_NAME = 'cheques' THEN NEW.cheque_number
                 ELSE NULL
            END,
            NEW.id::TEXT
        );

        -- Only log if actually changed
        IF v_old_values IS DISTINCT FROM v_new_values THEN
            PERFORM log_audit_event(
                v_tenant_id, NULL, v_action, TG_TABLE_NAME, NEW.id,
                v_entity_number, 'Updated ' || TG_TABLE_NAME,
                v_old_values, v_new_values, 'data_change', 'info'
            );
        END IF;
        RETURN NEW;

    ELSIF TG_OP = 'DELETE' THEN
        v_action := 'delete';
        v_old_values := to_jsonb(OLD);
        v_tenant_id := OLD.tenant_id;

        v_entity_number := COALESCE(
            CASE WHEN TG_TABLE_NAME = 'sales_invoices' THEN OLD.invoice_number
                 WHEN TG_TABLE_NAME = 'bills' THEN OLD.bill_number
                 WHEN TG_TABLE_NAME = 'journal_entries' THEN OLD.journal_number
                 WHEN TG_TABLE_NAME = 'fixed_assets' THEN OLD.asset_number
                 WHEN TG_TABLE_NAME = 'vendor_deposits' THEN OLD.deposit_number
                 WHEN TG_TABLE_NAME = 'customer_deposits' THEN OLD.deposit_number
                 WHEN TG_TABLE_NAME = 'cheques' THEN OLD.cheque_number
                 ELSE NULL
            END,
            OLD.id::TEXT
        );

        PERFORM log_audit_event(
            v_tenant_id, NULL, v_action, TG_TABLE_NAME, OLD.id,
            v_entity_number, 'Deleted ' || TG_TABLE_NAME,
            v_old_values, NULL, 'data_change', 'warning'
        );
        RETURN OLD;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- APPLY AUDIT TRIGGERS TO KEY TABLES
-- ============================================================================

-- Journal Entries - Critical accounting table
CREATE TRIGGER audit_journal_entries
AFTER INSERT OR UPDATE OR DELETE ON journal_entries
FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

-- Sales Invoices
CREATE TRIGGER audit_sales_invoices
AFTER INSERT OR UPDATE OR DELETE ON sales_invoices
FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

-- Bills
CREATE TRIGGER audit_bills
AFTER INSERT OR UPDATE OR DELETE ON bills
FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

-- Fixed Assets
CREATE TRIGGER audit_fixed_assets
AFTER INSERT OR UPDATE OR DELETE ON fixed_assets
FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

-- Vendor Deposits
CREATE TRIGGER audit_vendor_deposits
AFTER INSERT OR UPDATE OR DELETE ON vendor_deposits
FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

-- Customer Deposits
CREATE TRIGGER audit_customer_deposits
AFTER INSERT OR UPDATE OR DELETE ON customer_deposits
FOR EACH ROW EXECUTE FUNCTION audit_trigger_func();

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Get audit history for entity
CREATE OR REPLACE FUNCTION get_entity_audit_history(
    p_entity_type VARCHAR(100),
    p_entity_id UUID,
    p_limit INTEGER DEFAULT 50
)
RETURNS TABLE (
    id UUID,
    event_time TIMESTAMPTZ,
    action VARCHAR(50),
    user_email VARCHAR(255),
    description TEXT,
    changed_fields TEXT[],
    old_values JSONB,
    new_values JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        al.id,
        al.event_time,
        al.action,
        al.user_email,
        al.description,
        al.changed_fields,
        al.old_values,
        al.new_values
    FROM audit_logs al
    WHERE al.entity_type = p_entity_type
    AND al.entity_id = p_entity_id
    ORDER BY al.event_time DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get user activity
CREATE OR REPLACE FUNCTION get_user_activity(
    p_user_id UUID,
    p_from_date TIMESTAMPTZ DEFAULT NULL,
    p_to_date TIMESTAMPTZ DEFAULT NULL,
    p_limit INTEGER DEFAULT 100
)
RETURNS TABLE (
    id UUID,
    event_time TIMESTAMPTZ,
    action VARCHAR(50),
    entity_type VARCHAR(100),
    entity_number VARCHAR(100),
    description TEXT,
    category VARCHAR(50)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        al.id,
        al.event_time,
        al.action,
        al.entity_type,
        al.entity_number,
        al.description,
        al.category
    FROM audit_logs al
    WHERE al.user_id = p_user_id
    AND (p_from_date IS NULL OR al.event_time >= p_from_date)
    AND (p_to_date IS NULL OR al.event_time <= p_to_date)
    ORDER BY al.event_time DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Search audit logs (full-text)
CREATE OR REPLACE FUNCTION search_audit_logs(
    p_tenant_id TEXT,
    p_search_query TEXT,
    p_limit INTEGER DEFAULT 50
)
RETURNS TABLE (
    id UUID,
    event_time TIMESTAMPTZ,
    action VARCHAR(50),
    entity_type VARCHAR(100),
    entity_number VARCHAR(100),
    description TEXT,
    user_email VARCHAR(255),
    rank REAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        al.id,
        al.event_time,
        al.action,
        al.entity_type,
        al.entity_number,
        al.description,
        al.user_email,
        ts_rank(to_tsvector('english', al.search_text), plainto_tsquery('english', p_search_query)) as rank
    FROM audit_logs al
    WHERE al.tenant_id = p_tenant_id
    AND to_tsvector('english', al.search_text) @@ plainto_tsquery('english', p_search_query)
    ORDER BY rank DESC, al.event_time DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get audit summary
CREATE OR REPLACE FUNCTION get_audit_summary(
    p_tenant_id TEXT,
    p_from_date DATE DEFAULT NULL,
    p_to_date DATE DEFAULT NULL
)
RETURNS TABLE (
    action VARCHAR(50),
    entity_type VARCHAR(100),
    count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        al.action,
        al.entity_type,
        COUNT(*)::BIGINT
    FROM audit_logs al
    WHERE al.tenant_id = p_tenant_id
    AND (p_from_date IS NULL OR al.event_time::DATE >= p_from_date)
    AND (p_to_date IS NULL OR al.event_time::DATE <= p_to_date)
    GROUP BY al.action, al.entity_type
    ORDER BY COUNT(*) DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Cleanup old audit logs based on retention policy
CREATE OR REPLACE FUNCTION cleanup_audit_logs(p_tenant_id TEXT)
RETURNS INTEGER AS $$
DECLARE
    v_deleted INTEGER := 0;
    v_policy RECORD;
BEGIN
    FOR v_policy IN
        SELECT category, retention_days, delete_after_days
        FROM audit_retention_policies
        WHERE tenant_id = p_tenant_id AND is_active = true
    LOOP
        DELETE FROM audit_logs
        WHERE tenant_id = p_tenant_id
        AND category = v_policy.category
        AND event_time < NOW() - (COALESCE(v_policy.delete_after_days, v_policy.retention_days) || ' days')::INTERVAL;

        GET DIAGNOSTICS v_deleted = v_deleted + ROW_COUNT;
    END LOOP;

    RETURN v_deleted;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- COMMENTS
-- ============================================================================
COMMENT ON TABLE audit_logs IS 'Central audit log for all system activities and data changes';
COMMENT ON TABLE sensitive_data_access IS 'Tracks access to sensitive/PII data for compliance';
COMMENT ON TABLE login_history IS 'Records all login attempts for security monitoring';
COMMENT ON TABLE audit_retention_policies IS 'Configurable data retention policies per category';
COMMENT ON FUNCTION log_audit_event IS 'Utility function to create audit log entries programmatically';
COMMENT ON FUNCTION audit_trigger_func IS 'Generic trigger function for automatic audit logging';
