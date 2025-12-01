-- ============================================================
-- V002: Atomic Transaction Function
-- ============================================================
-- Purpose: Create transaction + items + outbox in ONE atomic operation
-- Target: Reduce DB latency from ~280-350ms to <150ms
-- Pattern: Transactional Outbox (Shopify, Uber, Stripe)
-- ============================================================

-- Drop existing function if exists (for idempotent migrations)
DROP FUNCTION IF EXISTS create_transaction_atomic CASCADE;

-- ============================================================
-- MAIN FUNCTION: create_transaction_atomic
-- ============================================================
CREATE OR REPLACE FUNCTION create_transaction_atomic(
    -- Core transaction fields
    p_id TEXT,
    p_tenant_id TEXT,
    p_created_by TEXT,
    p_actor_role TEXT,
    p_jenis_transaksi TEXT,
    p_payload JSONB,
    p_total_nominal BIGINT,
    p_metode_pembayaran TEXT,
    p_nama_pihak TEXT,
    p_keterangan TEXT,
    -- Discount & VAT fields
    p_discount_type TEXT DEFAULT NULL,
    p_discount_value FLOAT DEFAULT 0,
    p_discount_amount BIGINT DEFAULT 0,
    p_subtotal_before_discount BIGINT DEFAULT 0,
    p_subtotal_after_discount BIGINT DEFAULT 0,
    p_include_vat BOOLEAN DEFAULT FALSE,
    p_vat_amount BIGINT DEFAULT 0,
    p_grand_total BIGINT DEFAULT 0,
    -- Idempotency
    p_idempotency_key TEXT DEFAULT NULL,
    -- Additional SAK EMKM fields
    p_status_pembayaran TEXT DEFAULT NULL,
    p_nominal_dibayar BIGINT DEFAULT NULL,
    p_sisa_piutang_hutang BIGINT DEFAULT NULL,
    p_jatuh_tempo BIGINT DEFAULT NULL,
    p_kontak_pihak TEXT DEFAULT NULL,
    p_pihak_type TEXT DEFAULT NULL,
    p_lokasi_gudang TEXT DEFAULT NULL,
    p_kategori_arus_kas TEXT DEFAULT 'operasi',
    p_is_prive BOOLEAN DEFAULT FALSE,
    p_is_modal BOOLEAN DEFAULT FALSE,
    p_rekening_id TEXT DEFAULT NULL,
    p_rekening_type TEXT DEFAULT NULL,
    -- Arrays (JSONB)
    p_items JSONB DEFAULT '[]',
    p_outbox_events JSONB DEFAULT '[]'
) RETURNS TABLE (
    transaction_id TEXT,
    created_at TIMESTAMP,
    items_count INT,
    outbox_count INT,
    execution_time_ms FLOAT,
    is_idempotent BOOLEAN
) AS $$
DECLARE
    v_start_time TIMESTAMP;
    v_timestamp BIGINT;
    v_item JSONB;
    v_event JSONB;
    v_items_count INT := 0;
    v_outbox_count INT := 0;
    v_is_idempotent BOOLEAN := FALSE;
    v_existing_id TEXT;
    v_existing_created_at TIMESTAMP;
BEGIN
    v_start_time := clock_timestamp();

    -- Set RLS context ONCE for entire function
    PERFORM set_config('app.current_tenant_id', p_tenant_id, TRUE);
    PERFORM set_config('app.bypass_rls', 'true', TRUE);

    v_timestamp := (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT;

    -- ============================================================
    -- IDEMPOTENCY CHECK
    -- ============================================================
    IF p_idempotency_key IS NOT NULL AND p_idempotency_key != '' THEN
        SELECT th.id, th.created_at INTO v_existing_id, v_existing_created_at
        FROM transaksi_harian th
        WHERE th.idempotency_key = p_idempotency_key
        LIMIT 1;

        IF v_existing_id IS NOT NULL THEN
            -- Return existing transaction (idempotent)
            RETURN QUERY
            SELECT
                v_existing_id,
                v_existing_created_at,
                0::INT,
                0::INT,
                (EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000)::FLOAT,
                TRUE;
            RETURN;
        END IF;
    END IF;

    -- ============================================================
    -- INSERT MAIN TRANSACTION
    -- ============================================================
    INSERT INTO transaksi_harian (
        id,
        tenant_id,
        created_by,
        actor_role,
        timestamp,
        jenis_transaksi,
        payload,
        status,
        -- SAK EMKM fields
        total_nominal,
        metode_pembayaran,
        status_pembayaran,
        nominal_dibayar,
        sisa_piutang_hutang,
        jatuh_tempo,
        nama_pihak,
        kontak_pihak,
        pihak_type,
        lokasi_gudang,
        kategori_arus_kas,
        is_prive,
        is_modal,
        keterangan,
        rekening_id,
        rekening_type,
        -- Discount & VAT
        discount_type,
        discount_value,
        discount_amount,
        subtotal_before_discount,
        subtotal_after_discount,
        include_vat,
        vat_amount,
        grand_total,
        -- Idempotency
        idempotency_key
    ) VALUES (
        p_id,
        p_tenant_id,
        p_created_by,
        p_actor_role,
        v_timestamp,
        p_jenis_transaksi,
        p_payload,
        'approved',
        -- SAK EMKM fields
        p_total_nominal,
        p_metode_pembayaran,
        p_status_pembayaran,
        p_nominal_dibayar,
        p_sisa_piutang_hutang,
        p_jatuh_tempo,
        p_nama_pihak,
        p_kontak_pihak,
        p_pihak_type,
        p_lokasi_gudang,
        COALESCE(p_kategori_arus_kas, 'operasi'),
        COALESCE(p_is_prive, FALSE),
        COALESCE(p_is_modal, FALSE),
        p_keterangan,
        p_rekening_id,
        p_rekening_type,
        -- Discount & VAT
        p_discount_type,
        COALESCE(p_discount_value, 0),
        COALESCE(p_discount_amount, 0),
        p_subtotal_before_discount,
        p_subtotal_after_discount,
        COALESCE(p_include_vat, FALSE),
        COALESCE(p_vat_amount, 0),
        p_grand_total,
        -- Idempotency
        NULLIF(p_idempotency_key, '')
    );

    -- ============================================================
    -- INSERT ITEMS (loop through JSONB array)
    -- ============================================================
    FOR v_item IN SELECT * FROM jsonb_array_elements(p_items)
    LOOP
        INSERT INTO item_transaksi (
            id,
            transaksi_id,
            nama_produk,
            jumlah,
            satuan,
            harga_satuan,
            subtotal,
            produk_id,
            keterangan,
            hpp_per_unit,
            harga_jual,
            margin,
            margin_percent
        ) VALUES (
            COALESCE(v_item->>'id', gen_random_uuid()::TEXT),
            p_id,
            v_item->>'nama_produk',
            COALESCE((v_item->>'jumlah')::FLOAT, 0),
            v_item->>'satuan',
            COALESCE((v_item->>'harga_satuan')::BIGINT, 0),
            COALESCE((v_item->>'subtotal')::BIGINT, 0),
            NULLIF(v_item->>'produk_id', ''),
            v_item->>'keterangan',
            (v_item->>'hpp_per_unit')::FLOAT,
            (v_item->>'harga_jual')::FLOAT,
            (v_item->>'margin')::FLOAT,
            (v_item->>'margin_percent')::FLOAT
        );
        v_items_count := v_items_count + 1;
    END LOOP;

    -- ============================================================
    -- INSERT OUTBOX EVENTS (loop through JSONB array)
    -- ============================================================
    FOR v_event IN SELECT * FROM jsonb_array_elements(p_outbox_events)
    LOOP
        INSERT INTO outbox (
            id,
            transaksi_id,
            event_type,
            payload,
            processed,
            retry_count,
            created_at
        ) VALUES (
            gen_random_uuid()::TEXT,
            p_id,
            v_event->>'event_type',
            v_event->'payload',
            FALSE,
            0,
            NOW()
        );
        v_outbox_count := v_outbox_count + 1;
    END LOOP;

    -- ============================================================
    -- NOTIFY OUTBOX WORKER (for LISTEN/NOTIFY pattern)
    -- ============================================================
    IF v_outbox_count > 0 THEN
        PERFORM pg_notify(
            'outbox_events',
            json_build_object(
                'tenant_id', p_tenant_id,
                'transaction_id', p_id,
                'event_count', v_outbox_count,
                'timestamp', v_timestamp
            )::TEXT
        );
    END IF;

    -- ============================================================
    -- RETURN RESULT
    -- ============================================================
    RETURN QUERY
    SELECT
        p_id,
        NOW()::TIMESTAMP,
        v_items_count,
        v_outbox_count,
        (EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000)::FLOAT,
        FALSE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================
-- INDEXES for performance
-- ============================================================

-- Index for idempotency lookup (most important)
CREATE INDEX IF NOT EXISTS idx_transaksi_idempotency
ON transaksi_harian(tenant_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;

-- Index for outbox worker polling
CREATE INDEX IF NOT EXISTS idx_outbox_pending
ON outbox(created_at, transaksi_id)
WHERE processed = FALSE;

-- Index for outbox by tenant (for tenant-specific processing)
CREATE INDEX IF NOT EXISTS idx_outbox_tenant_pending
ON outbox(transaksi_id, processed, created_at)
WHERE processed = FALSE;

-- ============================================================
-- GRANT PERMISSIONS
-- ============================================================
GRANT EXECUTE ON FUNCTION create_transaction_atomic TO milkyadmin;

-- ============================================================
-- VERIFICATION
-- ============================================================
DO $$
BEGIN
    RAISE NOTICE 'Function create_transaction_atomic created successfully';
    RAISE NOTICE 'Indexes created for performance optimization';
END $$;
