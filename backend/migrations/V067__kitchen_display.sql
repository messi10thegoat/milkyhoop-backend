-- =============================================
-- V067: Kitchen Display System (KDS)
-- Purpose: Manage kitchen order queue and display for F&B operations
-- =============================================

-- ============================================================================
-- 1. KDS STATIONS (Kitchen Stations/Screens)
-- ============================================================================

CREATE TABLE IF NOT EXISTS kds_stations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    station_code VARCHAR(50) NOT NULL,
    station_name VARCHAR(100) NOT NULL,

    -- Type
    station_type VARCHAR(50) DEFAULT 'kitchen', -- kitchen, bar, grill, pastry, expo

    -- Location
    branch_id UUID REFERENCES branches(id),

    -- Display settings
    display_columns INTEGER DEFAULT 4,
    auto_bump_minutes INTEGER, -- auto-complete after X minutes
    alert_threshold_minutes INTEGER DEFAULT 15, -- turn red after X minutes

    -- Categories this station handles
    category_ids UUID[],

    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_kds_stations UNIQUE(tenant_id, station_code)
);

-- ============================================================================
-- 2. KDS ORDERS (Orders in Queue)
-- ============================================================================

CREATE TABLE IF NOT EXISTS kds_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Source
    sales_invoice_id UUID REFERENCES sales_invoices(id),
    order_number VARCHAR(50) NOT NULL,
    table_id UUID, -- FK to tables if dine-in

    -- Customer info
    customer_name VARCHAR(100),
    order_type VARCHAR(50) DEFAULT 'dine_in', -- dine_in, takeaway, delivery

    -- Station assignment
    station_id UUID REFERENCES kds_stations(id),

    -- Timing
    received_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    served_at TIMESTAMPTZ,

    -- Status
    status VARCHAR(20) DEFAULT 'pending', -- pending, preparing, ready, served, cancelled
    priority INTEGER DEFAULT 5, -- 1=rush, 5=normal

    -- Notes
    special_instructions TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_kds_status CHECK (status IN ('pending', 'preparing', 'ready', 'served', 'cancelled'))
);

-- ============================================================================
-- 3. KDS ORDER ITEMS (Items in Order)
-- ============================================================================

CREATE TABLE IF NOT EXISTS kds_order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kds_order_id UUID NOT NULL REFERENCES kds_orders(id) ON DELETE CASCADE,

    -- Product
    product_id UUID NOT NULL REFERENCES products(id),
    product_name VARCHAR(255) NOT NULL,
    quantity DECIMAL(10,2) NOT NULL,

    -- Recipe reference
    recipe_id UUID REFERENCES recipes(id),

    -- Modifiers applied
    modifiers JSONB, -- [{modifier_id, modifier_name, price}]

    -- Status
    status VARCHAR(20) DEFAULT 'pending', -- pending, preparing, ready, cancelled
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    -- Station (can differ from order station)
    station_id UUID REFERENCES kds_stations(id),

    -- Notes
    item_notes TEXT,

    display_order INTEGER DEFAULT 0
);

-- ============================================================================
-- 4. KDS ITEM HISTORY (For Analytics)
-- ============================================================================

CREATE TABLE IF NOT EXISTS kds_item_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    kds_order_item_id UUID NOT NULL,
    product_id UUID NOT NULL REFERENCES products(id),
    station_id UUID REFERENCES kds_stations(id),

    -- Timing metrics
    received_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    -- Calculated
    wait_time_seconds INTEGER, -- received to started
    prep_time_seconds INTEGER, -- started to completed
    total_time_seconds INTEGER,

    -- Outcome
    was_cancelled BOOLEAN DEFAULT false,
    was_remade BOOLEAN DEFAULT false,

    order_date DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 5. KDS ALERTS
-- ============================================================================

CREATE TABLE IF NOT EXISTS kds_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    station_id UUID REFERENCES kds_stations(id),
    kds_order_id UUID REFERENCES kds_orders(id),

    alert_type VARCHAR(50) NOT NULL, -- overdue, rush, remake, special_instruction
    message TEXT,

    acknowledged BOOLEAN DEFAULT false,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by UUID,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE kds_stations ENABLE ROW LEVEL SECURITY;
ALTER TABLE kds_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE kds_order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE kds_item_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE kds_alerts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_kds_stations ON kds_stations;
CREATE POLICY rls_kds_stations ON kds_stations
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_kds_orders ON kds_orders;
CREATE POLICY rls_kds_orders ON kds_orders
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_kds_order_items ON kds_order_items;
CREATE POLICY rls_kds_order_items ON kds_order_items
    USING (kds_order_id IN (SELECT id FROM kds_orders WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_kds_item_history ON kds_item_history;
CREATE POLICY rls_kds_item_history ON kds_item_history
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_kds_alerts ON kds_alerts;
CREATE POLICY rls_kds_alerts ON kds_alerts
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_kds_stations_tenant ON kds_stations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_kds_orders_station ON kds_orders(station_id, status);
CREATE INDEX IF NOT EXISTS idx_kds_orders_status ON kds_orders(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_kds_orders_received ON kds_orders(received_at);
CREATE INDEX IF NOT EXISTS idx_kds_order_items_order ON kds_order_items(kds_order_id);
CREATE INDEX IF NOT EXISTS idx_kds_item_history_date ON kds_item_history(tenant_id, order_date);
CREATE INDEX IF NOT EXISTS idx_kds_alerts_station ON kds_alerts(station_id, acknowledged) WHERE acknowledged = false;

-- ============================================================================
-- FUNCTION: Update KDS Order Status Based on Items
-- ============================================================================

CREATE OR REPLACE FUNCTION update_kds_order_status()
RETURNS TRIGGER AS $$
DECLARE
    v_total_items INTEGER;
    v_ready_items INTEGER;
    v_order_status VARCHAR(20);
BEGIN
    -- Count items
    SELECT COUNT(*), COUNT(*) FILTER (WHERE status = 'ready')
    INTO v_total_items, v_ready_items
    FROM kds_order_items
    WHERE kds_order_id = COALESCE(NEW.kds_order_id, OLD.kds_order_id);

    -- Determine order status
    IF v_ready_items = v_total_items THEN
        v_order_status := 'ready';
    ELSIF v_ready_items > 0 THEN
        v_order_status := 'preparing';
    ELSE
        v_order_status := 'pending';
    END IF;

    -- Update order
    UPDATE kds_orders
    SET status = v_order_status,
        started_at = CASE WHEN v_order_status IN ('preparing', 'ready') AND started_at IS NULL THEN NOW() ELSE started_at END,
        completed_at = CASE WHEN v_order_status = 'ready' THEN NOW() ELSE completed_at END,
        updated_at = NOW()
    WHERE id = COALESCE(NEW.kds_order_id, OLD.kds_order_id);

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_kds_order_status ON kds_order_items;
CREATE TRIGGER trg_update_kds_order_status
AFTER UPDATE OF status ON kds_order_items
FOR EACH ROW
EXECUTE FUNCTION update_kds_order_status();

-- ============================================================================
-- NOTE: No journal entries - KDS is operational, not financial
-- ============================================================================
