-- =============================================
-- EVLOGIA SEED: 11_production_orders.sql
-- Purpose: Create 50+ Production Orders with all status variants
-- For FG Produksi items (EVL-001 to EVL-008)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_fg_product_ids UUID[];
    v_fg_names TEXT[];
    v_bom_ids UUID[];
    v_wh_atput_id UUID;
    v_po_id UUID;
    v_po_count INT := 0;
    v_random_idx INT;
    v_fg_id UUID;
    v_bom_id UUID;
    v_qty INT;
    v_po_date DATE;
    v_status TEXT;
    v_planned_material_cost BIGINT;
    v_planned_labor_cost BIGINT;
    v_planned_overhead_cost BIGINT;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating production orders for tenant: %', v_tenant_id;

    -- Get warehouse ID
    SELECT id INTO v_wh_atput_id FROM warehouses WHERE tenant_id = v_tenant_id AND code = 'WH-ATPUT';

    -- Get FG Produksi product IDs
    SELECT array_agg(id), array_agg(nama_produk)
    INTO v_fg_product_ids, v_fg_names
    FROM products
    WHERE tenant_id = v_tenant_id AND kategori = 'FG Produksi';

    -- Get corresponding BOM IDs
    SELECT array_agg(bom.id)
    INTO v_bom_ids
    FROM bill_of_materials bom
    WHERE bom.tenant_id = v_tenant_id;

    IF array_length(v_fg_product_ids, 1) IS NULL THEN
        RAISE NOTICE 'No FG Produksi products found!';
        RETURN;
    END IF;

    -- ==========================================
    -- Generate 50+ Production Orders
    -- Distribution: Nov (15), Dec (20), Jan (15)
    -- Status: draft(5), planned(10), released(10), in_progress(10), completed(12), cancelled(3)
    -- ==========================================

    -- NOVEMBER 2025 - 15 POs
    FOR i IN 1..15 LOOP
        v_po_date := '2025-11-01'::DATE + (random() * 29)::INT;
        v_po_id := gen_random_uuid();
        v_random_idx := 1 + (random() * (array_length(v_fg_product_ids, 1) - 1))::INT;
        v_fg_id := v_fg_product_ids[v_random_idx];
        v_qty := 20 + (random() * 80)::INT;

        -- Get BOM for this product
        SELECT id, labor_cost, overhead_cost INTO v_bom_id, v_planned_labor_cost, v_planned_overhead_cost
        FROM bill_of_materials
        WHERE tenant_id = v_tenant_id AND product_id = v_fg_id
        LIMIT 1;

        -- Estimate material cost (simplified)
        v_planned_material_cost := v_qty * 75000;  -- avg ~75k per unit

        -- Determine status
        IF i <= 2 THEN v_status := 'draft';
        ELSIF i <= 5 THEN v_status := 'planned';
        ELSIF i <= 8 THEN v_status := 'released';
        ELSIF i <= 11 THEN v_status := 'in_progress';
        ELSIF i <= 14 THEN v_status := 'completed';
        ELSE v_status := 'cancelled';
        END IF;

        -- Insert Production Order
        INSERT INTO production_orders (
            id, tenant_id, order_number, order_date,
            product_id, bom_id, planned_quantity, completed_quantity, scrapped_quantity,
            unit, planned_start_date, planned_end_date,
            actual_start_date, actual_end_date,
            warehouse_id,
            planned_material_cost, planned_labor_cost, planned_overhead_cost,
            actual_material_cost, actual_labor_cost, actual_overhead_cost,
            status, notes, created_at, updated_at
        ) VALUES (
            v_po_id, v_tenant_id,
            'WO-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_po_date,
            v_fg_id, v_bom_id, v_qty,
            CASE WHEN v_status = 'completed' THEN v_qty WHEN v_status = 'in_progress' THEN (v_qty * 0.6)::INT ELSE 0 END,
            CASE WHEN v_status = 'completed' THEN (v_qty * 0.02)::INT ELSE 0 END,
            'pcs', v_po_date, v_po_date + 7,
            CASE WHEN v_status IN ('in_progress', 'completed') THEN v_po_date + 1 ELSE NULL END,
            CASE WHEN v_status = 'completed' THEN v_po_date + 5 ELSE NULL END,
            v_wh_atput_id,
            v_planned_material_cost, v_planned_labor_cost * v_qty, v_planned_overhead_cost * v_qty,
            CASE WHEN v_status = 'completed' THEN (v_planned_material_cost * 1.05)::BIGINT ELSE 0 END,
            CASE WHEN v_status = 'completed' THEN (v_planned_labor_cost * v_qty * 1.02)::BIGINT ELSE 0 END,
            CASE WHEN v_status = 'completed' THEN (v_planned_overhead_cost * v_qty)::BIGINT ELSE 0 END,
            v_status,
            'Production Order November ' || i,
            v_po_date, v_po_date
        );

        -- Insert material requirements (from BOM)
        IF v_bom_id IS NOT NULL THEN
            INSERT INTO production_order_materials (
                id, production_order_id, product_id,
                planned_quantity, unit, planned_cost,
                issued_quantity, actual_cost,
                warehouse_id
            )
            SELECT
                gen_random_uuid(), v_po_id, bc.product_id,
                bc.quantity * v_qty, bc.unit, (p.purchase_price * bc.quantity * v_qty),
                CASE WHEN v_status IN ('in_progress', 'completed') THEN bc.quantity * v_qty ELSE 0 END,
                CASE WHEN v_status = 'completed' THEN (p.purchase_price * bc.quantity * v_qty * 1.05)::BIGINT ELSE 0 END,
                v_wh_atput_id
            FROM bom_components bc
            JOIN products p ON bc.product_id = p.id
            WHERE bc.bom_id = v_bom_id;
        END IF;

        v_po_count := v_po_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 20 POs
    FOR i IN 1..20 LOOP
        v_po_date := '2025-12-01'::DATE + (random() * 30)::INT;
        v_po_id := gen_random_uuid();
        v_random_idx := 1 + (random() * (array_length(v_fg_product_ids, 1) - 1))::INT;
        v_fg_id := v_fg_product_ids[v_random_idx];
        v_qty := 25 + (random() * 100)::INT;

        SELECT id, labor_cost, overhead_cost INTO v_bom_id, v_planned_labor_cost, v_planned_overhead_cost
        FROM bill_of_materials
        WHERE tenant_id = v_tenant_id AND product_id = v_fg_id
        LIMIT 1;

        v_planned_material_cost := v_qty * 75000;

        IF i <= 2 THEN v_status := 'draft';
        ELSIF i <= 5 THEN v_status := 'planned';
        ELSIF i <= 9 THEN v_status := 'released';
        ELSIF i <= 13 THEN v_status := 'in_progress';
        ELSIF i <= 19 THEN v_status := 'completed';
        ELSE v_status := 'cancelled';
        END IF;

        INSERT INTO production_orders (
            id, tenant_id, order_number, order_date,
            product_id, bom_id, planned_quantity, completed_quantity, scrapped_quantity,
            unit, planned_start_date, planned_end_date,
            actual_start_date, actual_end_date,
            warehouse_id,
            planned_material_cost, planned_labor_cost, planned_overhead_cost,
            actual_material_cost, actual_labor_cost, actual_overhead_cost,
            status, notes, created_at, updated_at
        ) VALUES (
            v_po_id, v_tenant_id,
            'WO-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_po_date,
            v_fg_id, v_bom_id, v_qty,
            CASE WHEN v_status = 'completed' THEN v_qty WHEN v_status = 'in_progress' THEN (v_qty * 0.5)::INT ELSE 0 END,
            CASE WHEN v_status = 'completed' THEN (v_qty * 0.03)::INT ELSE 0 END,
            'pcs', v_po_date, v_po_date + 7,
            CASE WHEN v_status IN ('in_progress', 'completed') THEN v_po_date + 1 ELSE NULL END,
            CASE WHEN v_status = 'completed' THEN v_po_date + 6 ELSE NULL END,
            v_wh_atput_id,
            v_planned_material_cost, COALESCE(v_planned_labor_cost, 35000) * v_qty, COALESCE(v_planned_overhead_cost, 10000) * v_qty,
            CASE WHEN v_status = 'completed' THEN (v_planned_material_cost * 1.03)::BIGINT ELSE 0 END,
            CASE WHEN v_status = 'completed' THEN (COALESCE(v_planned_labor_cost, 35000) * v_qty * 1.01)::BIGINT ELSE 0 END,
            CASE WHEN v_status = 'completed' THEN (COALESCE(v_planned_overhead_cost, 10000) * v_qty)::BIGINT ELSE 0 END,
            v_status,
            'Production Order December ' || i,
            v_po_date, v_po_date
        );

        IF v_bom_id IS NOT NULL THEN
            INSERT INTO production_order_materials (
                id, production_order_id, product_id,
                planned_quantity, unit, planned_cost,
                issued_quantity, actual_cost,
                warehouse_id
            )
            SELECT
                gen_random_uuid(), v_po_id, bc.product_id,
                bc.quantity * v_qty, bc.unit, (p.purchase_price * bc.quantity * v_qty),
                CASE WHEN v_status IN ('in_progress', 'completed') THEN bc.quantity * v_qty ELSE 0 END,
                CASE WHEN v_status = 'completed' THEN (p.purchase_price * bc.quantity * v_qty * 1.03)::BIGINT ELSE 0 END,
                v_wh_atput_id
            FROM bom_components bc
            JOIN products p ON bc.product_id = p.id
            WHERE bc.bom_id = v_bom_id;
        END IF;

        v_po_count := v_po_count + 1;
    END LOOP;

    -- JANUARY 2026 - 15 POs
    FOR i IN 1..15 LOOP
        v_po_date := '2026-01-01'::DATE + (random() * 15)::INT;
        v_po_id := gen_random_uuid();
        v_random_idx := 1 + (random() * (array_length(v_fg_product_ids, 1) - 1))::INT;
        v_fg_id := v_fg_product_ids[v_random_idx];
        v_qty := 15 + (random() * 50)::INT;

        SELECT id, labor_cost, overhead_cost INTO v_bom_id, v_planned_labor_cost, v_planned_overhead_cost
        FROM bill_of_materials
        WHERE tenant_id = v_tenant_id AND product_id = v_fg_id
        LIMIT 1;

        v_planned_material_cost := v_qty * 75000;

        IF i <= 2 THEN v_status := 'draft';
        ELSIF i <= 5 THEN v_status := 'planned';
        ELSIF i <= 8 THEN v_status := 'released';
        ELSIF i <= 11 THEN v_status := 'in_progress';
        ELSE v_status := 'completed';
        END IF;

        INSERT INTO production_orders (
            id, tenant_id, order_number, order_date,
            product_id, bom_id, planned_quantity, completed_quantity, scrapped_quantity,
            unit, planned_start_date, planned_end_date,
            actual_start_date, actual_end_date,
            warehouse_id,
            planned_material_cost, planned_labor_cost, planned_overhead_cost,
            actual_material_cost, actual_labor_cost, actual_overhead_cost,
            status, notes, created_at, updated_at
        ) VALUES (
            v_po_id, v_tenant_id,
            'WO-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_po_date,
            v_fg_id, v_bom_id, v_qty,
            CASE WHEN v_status = 'completed' THEN v_qty WHEN v_status = 'in_progress' THEN (v_qty * 0.4)::INT ELSE 0 END,
            0,
            'pcs', v_po_date, v_po_date + 7,
            CASE WHEN v_status IN ('in_progress', 'completed') THEN v_po_date + 1 ELSE NULL END,
            CASE WHEN v_status = 'completed' THEN v_po_date + 5 ELSE NULL END,
            v_wh_atput_id,
            v_planned_material_cost, COALESCE(v_planned_labor_cost, 35000) * v_qty, COALESCE(v_planned_overhead_cost, 10000) * v_qty,
            CASE WHEN v_status = 'completed' THEN v_planned_material_cost ELSE 0 END,
            CASE WHEN v_status = 'completed' THEN (COALESCE(v_planned_labor_cost, 35000) * v_qty)::BIGINT ELSE 0 END,
            CASE WHEN v_status = 'completed' THEN (COALESCE(v_planned_overhead_cost, 10000) * v_qty)::BIGINT ELSE 0 END,
            v_status,
            'Production Order January ' || i,
            v_po_date, v_po_date
        );

        IF v_bom_id IS NOT NULL THEN
            INSERT INTO production_order_materials (
                id, production_order_id, product_id,
                planned_quantity, unit, planned_cost,
                issued_quantity, actual_cost,
                warehouse_id
            )
            SELECT
                gen_random_uuid(), v_po_id, bc.product_id,
                bc.quantity * v_qty, bc.unit, (p.purchase_price * bc.quantity * v_qty),
                CASE WHEN v_status IN ('in_progress', 'completed') THEN bc.quantity * v_qty ELSE 0 END,
                CASE WHEN v_status = 'completed' THEN (p.purchase_price * bc.quantity * v_qty)::BIGINT ELSE 0 END,
                v_wh_atput_id
            FROM bom_components bc
            JOIN products p ON bc.product_id = p.id
            WHERE bc.bom_id = v_bom_id;
        END IF;

        v_po_count := v_po_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Production Orders created: %', v_po_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Production Orders by status
SELECT status, COUNT(*) as count, SUM(planned_quantity) as total_planned, SUM(completed_quantity) as total_completed
FROM production_orders
WHERE tenant_id = 'evlogia'
GROUP BY status
ORDER BY status;

-- Verify by product
SELECT
    p.kode_produk,
    p.nama_produk,
    COUNT(po.id) as order_count,
    SUM(po.planned_quantity) as total_planned
FROM production_orders po
JOIN products p ON po.product_id = p.id
WHERE po.tenant_id = 'evlogia'
GROUP BY p.kode_produk, p.nama_produk
ORDER BY p.kode_produk;
