-- =============================================
-- V068: Table Management (Manajemen Meja)
-- Purpose: Manage restaurant tables, reservations, and seating for F&B
-- =============================================

-- ============================================================================
-- 1. TABLE AREAS/SECTIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS table_areas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    area_code VARCHAR(50) NOT NULL,
    area_name VARCHAR(100) NOT NULL,
    description TEXT,

    -- Location
    branch_id UUID REFERENCES branches(id),
    floor_number INTEGER DEFAULT 1,

    -- Capacity
    total_tables INTEGER DEFAULT 0,
    total_capacity INTEGER DEFAULT 0,

    -- Settings
    is_smoking BOOLEAN DEFAULT false,
    is_outdoor BOOLEAN DEFAULT false,
    is_private BOOLEAN DEFAULT false,

    is_active BOOLEAN DEFAULT true,
    display_order INTEGER DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_table_areas UNIQUE(tenant_id, area_code)
);

-- ============================================================================
-- 2. TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS restaurant_tables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    table_number VARCHAR(20) NOT NULL,
    table_name VARCHAR(100),

    -- Area
    area_id UUID REFERENCES table_areas(id),

    -- Capacity
    min_capacity INTEGER DEFAULT 1,
    max_capacity INTEGER NOT NULL,

    -- Position (for floor plan)
    position_x INTEGER,
    position_y INTEGER,
    shape VARCHAR(20) DEFAULT 'rectangle', -- rectangle, circle, square

    -- Current status
    status VARCHAR(20) DEFAULT 'available', -- available, occupied, reserved, cleaning, blocked
    current_order_id UUID, -- Link to current kds_order or sales_invoice
    occupied_at TIMESTAMPTZ,
    customer_name VARCHAR(100),
    guest_count INTEGER,

    -- Settings
    is_combinable BOOLEAN DEFAULT true, -- can be combined with adjacent tables
    requires_deposit BOOLEAN DEFAULT false,
    minimum_spend BIGINT,

    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_restaurant_tables UNIQUE(tenant_id, table_number),
    CONSTRAINT chk_table_status CHECK (status IN ('available', 'occupied', 'reserved', 'cleaning', 'blocked'))
);

-- ============================================================================
-- 3. RESERVATIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS table_reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    reservation_number VARCHAR(50) NOT NULL,

    -- Customer
    customer_id UUID,
    customer_name VARCHAR(100) NOT NULL,
    customer_phone VARCHAR(50),
    customer_email VARCHAR(255),

    -- Reservation details
    reservation_date DATE NOT NULL,
    reservation_time TIME NOT NULL,
    duration_minutes INTEGER DEFAULT 120, -- expected duration
    party_size INTEGER NOT NULL,

    -- Table assignment
    table_id UUID REFERENCES restaurant_tables(id),
    area_preference UUID REFERENCES table_areas(id),

    -- Status
    status VARCHAR(20) DEFAULT 'pending', -- pending, confirmed, seated, completed, cancelled, no_show

    -- Deposit
    deposit_amount BIGINT DEFAULT 0,
    deposit_paid BOOLEAN DEFAULT false,
    deposit_payment_id UUID,

    -- Notes
    special_requests TEXT,
    occasion VARCHAR(50), -- birthday, anniversary, business, etc
    internal_notes TEXT,

    -- Reminders
    reminder_sent BOOLEAN DEFAULT false,
    confirmation_sent BOOLEAN DEFAULT false,

    -- Timing
    confirmed_at TIMESTAMPTZ,
    seated_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    cancellation_reason TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_table_reservations UNIQUE(tenant_id, reservation_number),
    CONSTRAINT chk_reservation_status CHECK (status IN ('pending', 'confirmed', 'seated', 'completed', 'cancelled', 'no_show'))
);

-- ============================================================================
-- 4. TABLE SESSIONS (Track each table use)
-- ============================================================================

CREATE TABLE IF NOT EXISTS table_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    table_id UUID NOT NULL REFERENCES restaurant_tables(id),
    reservation_id UUID REFERENCES table_reservations(id),

    -- Session info
    seated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    vacated_at TIMESTAMPTZ,

    -- Guests
    guest_count INTEGER NOT NULL,
    customer_name VARCHAR(100),

    -- Orders
    sales_invoice_id UUID REFERENCES sales_invoices(id),
    kds_order_id UUID REFERENCES kds_orders(id),
    total_amount BIGINT DEFAULT 0,

    -- Staff
    server_id UUID,
    server_name VARCHAR(100),

    -- Notes
    notes TEXT,

    session_date DATE NOT NULL DEFAULT CURRENT_DATE
);

-- ============================================================================
-- 5. WAITLIST
-- ============================================================================

CREATE TABLE IF NOT EXISTS table_waitlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Customer
    customer_name VARCHAR(100) NOT NULL,
    customer_phone VARCHAR(50),
    party_size INTEGER NOT NULL,

    -- Preferences
    area_preference UUID REFERENCES table_areas(id),

    -- Status
    status VARCHAR(20) DEFAULT 'waiting', -- waiting, notified, seated, cancelled
    position INTEGER,

    -- Timing
    joined_at TIMESTAMPTZ DEFAULT NOW(),
    estimated_wait_minutes INTEGER,
    notified_at TIMESTAMPTZ,
    seated_at TIMESTAMPTZ,

    -- Assignment
    assigned_table_id UUID REFERENCES restaurant_tables(id),

    notes TEXT,

    CONSTRAINT chk_waitlist_status CHECK (status IN ('waiting', 'notified', 'seated', 'cancelled'))
);

-- ============================================================================
-- 6. RESERVATION SEQUENCES
-- ============================================================================

CREATE TABLE IF NOT EXISTS reservation_sequences (
    tenant_id TEXT PRIMARY KEY,
    last_number INTEGER DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'RES',
    last_reset_year INTEGER
);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE table_areas ENABLE ROW LEVEL SECURITY;
ALTER TABLE restaurant_tables ENABLE ROW LEVEL SECURITY;
ALTER TABLE table_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE table_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE table_waitlist ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_table_areas ON table_areas;
CREATE POLICY rls_table_areas ON table_areas
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_restaurant_tables ON restaurant_tables;
CREATE POLICY rls_restaurant_tables ON restaurant_tables
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_table_reservations ON table_reservations;
CREATE POLICY rls_table_reservations ON table_reservations
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_table_sessions ON table_sessions;
CREATE POLICY rls_table_sessions ON table_sessions
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_table_waitlist ON table_waitlist;
CREATE POLICY rls_table_waitlist ON table_waitlist
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_table_areas_tenant ON table_areas(tenant_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_tables_area ON restaurant_tables(area_id);
CREATE INDEX IF NOT EXISTS idx_restaurant_tables_status ON restaurant_tables(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_table_reservations_date ON table_reservations(tenant_id, reservation_date);
CREATE INDEX IF NOT EXISTS idx_table_reservations_status ON table_reservations(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_table_reservations_table ON table_reservations(table_id);
CREATE INDEX IF NOT EXISTS idx_table_sessions_table ON table_sessions(table_id);
CREATE INDEX IF NOT EXISTS idx_table_sessions_date ON table_sessions(tenant_id, session_date);
CREATE INDEX IF NOT EXISTS idx_table_waitlist_status ON table_waitlist(tenant_id, status) WHERE status = 'waiting';

-- ============================================================================
-- FUNCTION: Generate Reservation Number
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_reservation_number(p_tenant_id TEXT)
RETURNS TEXT AS $$
DECLARE
    v_prefix VARCHAR(10);
    v_number INTEGER;
    v_year INTEGER;
BEGIN
    v_year := EXTRACT(YEAR FROM CURRENT_DATE);

    INSERT INTO reservation_sequences (tenant_id, last_number, prefix, last_reset_year)
    VALUES (p_tenant_id, 1, 'RES', v_year)
    ON CONFLICT (tenant_id) DO UPDATE SET
        last_number = CASE
            WHEN reservation_sequences.last_reset_year != v_year THEN 1
            ELSE reservation_sequences.last_number + 1
        END,
        last_reset_year = v_year
    RETURNING prefix, last_number INTO v_prefix, v_number;

    RETURN v_prefix || '-' || v_year || '-' || LPAD(v_number::TEXT, 5, '0');
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- FUNCTION: Update Table Status
-- ============================================================================

CREATE OR REPLACE FUNCTION update_table_status_from_reservation()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'seated' AND OLD.status != 'seated' THEN
        UPDATE restaurant_tables
        SET status = 'occupied',
            occupied_at = NOW(),
            customer_name = NEW.customer_name,
            guest_count = NEW.party_size,
            updated_at = NOW()
        WHERE id = NEW.table_id;

        -- Create session
        INSERT INTO table_sessions (tenant_id, table_id, reservation_id, guest_count, customer_name, seated_at)
        SELECT tenant_id, NEW.table_id, NEW.id, NEW.party_size, NEW.customer_name, NOW()
        FROM table_reservations WHERE id = NEW.id;

    ELSIF NEW.status = 'completed' AND OLD.status = 'seated' THEN
        UPDATE restaurant_tables
        SET status = 'cleaning',
            updated_at = NOW()
        WHERE id = NEW.table_id;

        -- Update session
        UPDATE table_sessions
        SET vacated_at = NOW()
        WHERE reservation_id = NEW.id AND vacated_at IS NULL;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_table_from_reservation ON table_reservations;
CREATE TRIGGER trg_update_table_from_reservation
AFTER UPDATE OF status ON table_reservations
FOR EACH ROW
EXECUTE FUNCTION update_table_status_from_reservation();

-- ============================================================================
-- NOTE: No direct journal entries - links to sales_invoices for financial
-- ============================================================================
