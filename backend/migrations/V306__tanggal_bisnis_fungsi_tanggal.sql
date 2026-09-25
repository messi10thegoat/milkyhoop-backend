-- V306 (backlog 3b, putusan MASTER 25 Sep 2026: A+B) — "hari ini" di 15 fungsi DB = tanggal BISNIS
-- tenant, bukan UTC.
--
-- Postgres berjalan UTC (TimeZone Etc/UTC; pool tak menyetel zona): pukul 00.00–07.00 WIB
-- CURRENT_DATE = KEMARIN. V302 (tanggal_bisnis) / V303 (AR/AP) sudah menutup penomoran & AR/AP;
-- fungsi-fungsi ini tertinggal.
-- Dampak terukur 25 Sep: ≈0 — tabel pendukung KOSONG (item_batches, cheques, price_lists
-- bertanggal, asset_maintenance, recurring_bills, stock_transfers, item_serials,
-- sales_invoice_payments); quotes 31 baris tapi 0 berstatus 'sent'. Penjaga yang bertahan karena
-- tabel kosong bukan penjaga → diperbaiki sekarang (laten).
-- Isi = definisi LIVE (pg_get_functiondef 25 Sep 2026 22:2x WIB) dengan SATU substitusi per fungsi:
--   A  (ber-p_tenant_id)  CURRENT_DATE -> tanggal_bisnis(p_tenant_id):
--      get_expiring_batches, get_expired_batches, get_cheque_aging, get_upcoming_cheques,
--      get_item_price, get_maintenance_due, get_recurring_bill_stats, get_current_open_period,
--      mark_serials_sold
--   B  (tenant di tangan)
--      auto_expire_batches, check_quote_expiry   -> tanggal_bisnis(NEW.tenant_id)
--      process_expired_batches                   -> tanggal_bisnis(r.tenant_id)  (per tenant di loop)
--      ship_stock_transfer, receive_stock_transfer -> tanggal_bisnis(v_tenant_id)
--      update_sales_invoice_status               -> tanggal_bisnis(tenant faktur NEW.invoice_id)
-- TIDAK disentuh (backlog C: tanpa tenant, butuh ubah signature): calculate_bill_status,
--   can_user_approve, get_branch_document_number. generate_document_key: CURRENT_DATE = jalur
--   PENYIMPANAN (YYYY/MM), bukan tanggal bisnis → dibiarkan.
-- SUBSTITUSI KEDUA (hanya get_expiring_batches): `i.name` -> COALESCE(i.nama_produk, '(produk tidak
--   ditemukan)')::character varying — tabel products tak punya kolom `name`, jadi fungsi ini GALAT di prod
--   pada SETIAP panggilan (terukur 25 Sep, juga dengan 0 baris: "column i.name does not exist") →
--   GET /api/item-batches/expiring = 500. Pola disalin dari kembarannya get_expired_batches (sudah benar).
--   Ditemukan oleh gerbang V306 (eksekusi nyata), bukan oleh tiket.
-- Catatan ukur: get_available_batches TIDAK memakai CURRENT_DATE (klaim tiket 3b keliru).
-- Gerbang: scripts/gate_v306.sh (DB scratch; tanpa migrasi = KONTROL MERAH).

-- auto_expire_batches: 1x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.auto_expire_batches()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    -- Check if expiry date has passed
    IF NEW.expiry_date IS NOT NULL AND NEW.expiry_date < tanggal_bisnis(NEW.tenant_id) AND OLD.status = 'active' THEN
        NEW.status := 'expired';
    END IF;

    -- Check if depleted
    IF NEW.current_quantity <= 0 AND OLD.status = 'active' THEN
        NEW.status := 'depleted';
    END IF;

    RETURN NEW;
END;
$function$
;

-- check_quote_expiry: 1x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.check_quote_expiry()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    -- Auto-expire sent quotes past expiry date
    IF NEW.expiry_date IS NOT NULL
       AND NEW.expiry_date < tanggal_bisnis(NEW.tenant_id)
       AND OLD.status = 'sent'
       AND NEW.status = 'sent' THEN
        NEW.status := 'expired';
    END IF;
    RETURN NEW;
END;
$function$
;

-- get_cheque_aging: 4x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.get_cheque_aging(p_tenant_id text, p_cheque_type character varying DEFAULT 'received'::character varying)
 RETURNS TABLE(aging_bucket character varying, count bigint, total_amount bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        CASE
            WHEN cheque_date > tanggal_bisnis(p_tenant_id) THEN 'future'
            WHEN (tanggal_bisnis(p_tenant_id) - cheque_date) BETWEEN 0 AND 30 THEN '0-30 days'
            WHEN (tanggal_bisnis(p_tenant_id) - cheque_date) BETWEEN 31 AND 60 THEN '31-60 days'
            WHEN (tanggal_bisnis(p_tenant_id) - cheque_date) BETWEEN 61 AND 90 THEN '61-90 days'
            ELSE '90+ days'
        END as aging_bucket,
        COUNT(*)::BIGINT,
        COALESCE(SUM(amount), 0)::BIGINT
    FROM cheques
    WHERE tenant_id = p_tenant_id
    AND cheque_type = p_cheque_type
    AND status = 'pending'
    GROUP BY aging_bucket
    ORDER BY aging_bucket;
END;
$function$
;

-- get_current_open_period: 1x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.get_current_open_period(p_tenant_id text)
 RETURNS TABLE(id uuid, period_name text, start_date date, end_date date, status text, fiscal_year_id uuid)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    RETURN QUERY
    SELECT fp.id, fp.period_name, fp.start_date, fp.end_date, fp.status, fp.fiscal_year_id
    FROM fiscal_periods fp
    WHERE fp.tenant_id = p_tenant_id
      AND fp.status = 'OPEN'
      AND tanggal_bisnis(p_tenant_id) BETWEEN fp.start_date AND fp.end_date
    ORDER BY fp.start_date DESC
    LIMIT 1;
END;
$function$
;

-- get_expired_batches: 2x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.get_expired_batches(p_tenant_id text, p_warehouse_id uuid DEFAULT NULL::uuid)
 RETURNS TABLE(batch_id uuid, item_id uuid, item_name character varying, batch_number character varying, expiry_date date, days_expired integer, quantity numeric, total_value numeric, warehouse_id uuid, warehouse_name character varying)
 LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        ib.id as batch_id,
        ib.item_id,
        COALESCE(i.nama_produk, '(produk tidak ditemukan)')::character varying as item_name,
        ib.batch_number,
        ib.expiry_date,
        (tanggal_bisnis(p_tenant_id) - ib.expiry_date)::INTEGER as days_expired,
        bws.quantity,
        ROUND(bws.quantity * COALESCE(ib.unit_cost, 0), 2)::numeric(18,2) as total_value,
        bws.warehouse_id,
        w.name as warehouse_name
    FROM item_batches ib
    JOIN batch_warehouse_stock bws ON ib.id = bws.batch_id
    JOIN warehouses w ON bws.warehouse_id = w.id
    LEFT JOIN products i ON ib.item_id = i.id
    WHERE ib.tenant_id = p_tenant_id
    AND ib.expiry_date IS NOT NULL
    AND ib.expiry_date < tanggal_bisnis(p_tenant_id)
    AND bws.quantity > 0
    AND (p_warehouse_id IS NULL OR bws.warehouse_id = p_warehouse_id)
    ORDER BY ib.expiry_date ASC;
END;
$function$
;

-- get_expiring_batches: 2x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.get_expiring_batches(p_tenant_id text, p_days_ahead integer DEFAULT 30, p_warehouse_id uuid DEFAULT NULL::uuid)
 RETURNS TABLE(batch_id uuid, item_id uuid, item_name character varying, batch_number character varying, expiry_date date, days_until_expiry integer, quantity numeric, warehouse_id uuid, warehouse_name character varying)
 LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        ib.id as batch_id,
        ib.item_id,
        COALESCE(i.nama_produk, '(produk tidak ditemukan)')::character varying as item_name,
        ib.batch_number,
        ib.expiry_date,
        (ib.expiry_date - tanggal_bisnis(p_tenant_id))::INTEGER as days_until_expiry,
        bws.quantity,
        bws.warehouse_id,
        w.name as warehouse_name
    FROM item_batches ib
    JOIN batch_warehouse_stock bws ON ib.id = bws.batch_id
    JOIN warehouses w ON bws.warehouse_id = w.id
    LEFT JOIN products i ON ib.item_id = i.id
    WHERE ib.tenant_id = p_tenant_id
    AND ib.status = 'active'
    AND ib.expiry_date IS NOT NULL
    AND ib.expiry_date <= (tanggal_bisnis(p_tenant_id) + (p_days_ahead || ' days')::INTERVAL)
    AND bws.quantity > 0
    AND (p_warehouse_id IS NULL OR bws.warehouse_id = p_warehouse_id)
    ORDER BY ib.expiry_date ASC, bws.quantity DESC;
END;
$function$
;

-- get_item_price: 4x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.get_item_price(p_tenant_id text, p_item_id uuid, p_customer_id uuid DEFAULT NULL::uuid, p_quantity numeric DEFAULT 1, p_unit character varying DEFAULT NULL::character varying)
 RETURNS bigint
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_price BIGINT;
BEGIN
    -- First try customer-specific price list
    IF p_customer_id IS NOT NULL THEN
        SELECT pli.price INTO v_price
        FROM price_list_items pli
        JOIN price_lists pl ON pli.price_list_id = pl.id
        JOIN customer_price_lists cpl ON pl.id = cpl.price_list_id
        WHERE pl.tenant_id = p_tenant_id
          AND cpl.customer_id = p_customer_id
          AND cpl.is_active = true
          AND pli.item_id = p_item_id
          AND pli.is_active = true
          AND pl.is_active = true
          AND (pli.unit = p_unit OR pli.unit IS NULL OR p_unit IS NULL)
          AND pli.min_quantity <= p_quantity
          AND (pl.start_date IS NULL OR pl.start_date <= tanggal_bisnis(p_tenant_id))
          AND (pl.end_date IS NULL OR pl.end_date >= tanggal_bisnis(p_tenant_id))
        ORDER BY cpl.priority ASC, pli.min_quantity DESC
        LIMIT 1;

        IF v_price IS NOT NULL THEN
            RETURN v_price;
        END IF;
    END IF;

    -- Fall back to default price list
    SELECT pli.price INTO v_price
    FROM price_list_items pli
    JOIN price_lists pl ON pli.price_list_id = pl.id
    WHERE pl.tenant_id = p_tenant_id
      AND pl.is_default = true
      AND pl.is_active = true
      AND pli.item_id = p_item_id
      AND pli.is_active = true
      AND (pli.unit = p_unit OR pli.unit IS NULL OR p_unit IS NULL)
      AND pli.min_quantity <= p_quantity
      AND (pl.start_date IS NULL OR pl.start_date <= tanggal_bisnis(p_tenant_id))
      AND (pl.end_date IS NULL OR pl.end_date >= tanggal_bisnis(p_tenant_id))
    ORDER BY pli.min_quantity DESC
    LIMIT 1;

    RETURN v_price;
END;
$function$
;

-- get_maintenance_due: 2x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.get_maintenance_due(p_tenant_id text, p_days_ahead integer DEFAULT 30)
 RETURNS TABLE(asset_id uuid, asset_number character varying, asset_name character varying, last_maintenance_date date, next_maintenance_date date, maintenance_type character varying, days_until integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT DISTINCT ON (fa.id)
        fa.id as asset_id,
        fa.asset_number,
        fa.name as asset_name,
        am.maintenance_date as last_maintenance_date,
        am.next_maintenance_date,
        am.maintenance_type,
        (am.next_maintenance_date - tanggal_bisnis(p_tenant_id))::INTEGER as days_until
    FROM fixed_assets fa
    JOIN asset_maintenance am ON fa.id = am.asset_id
    WHERE fa.tenant_id = p_tenant_id
    AND fa.status = 'active'
    AND am.next_maintenance_date IS NOT NULL
    AND am.next_maintenance_date <= tanggal_bisnis(p_tenant_id) + (p_days_ahead || ' days')::INTERVAL
    ORDER BY fa.id, am.maintenance_date DESC;
END;
$function$
;

-- get_recurring_bill_stats: 7x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.get_recurring_bill_stats(p_tenant_id text)
 RETURNS TABLE(total_active integer, total_paused integer, total_completed integer, bills_generated_this_month integer, total_amount_this_month bigint, due_today integer, due_this_week integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT COUNT(*)::INTEGER FROM recurring_bills WHERE tenant_id = p_tenant_id AND status = 'active'),
        (SELECT COUNT(*)::INTEGER FROM recurring_bills WHERE tenant_id = p_tenant_id AND status = 'paused'),
        (SELECT COUNT(*)::INTEGER FROM recurring_bills WHERE tenant_id = p_tenant_id AND status = 'completed'),
        (SELECT COUNT(*)::INTEGER FROM bills WHERE tenant_id = p_tenant_id
            AND is_recurring = true AND EXTRACT(MONTH FROM bill_date) = EXTRACT(MONTH FROM tanggal_bisnis(p_tenant_id))
            AND EXTRACT(YEAR FROM bill_date) = EXTRACT(YEAR FROM tanggal_bisnis(p_tenant_id))),
        (SELECT COALESCE(SUM(total_amount), 0)::BIGINT FROM bills WHERE tenant_id = p_tenant_id
            AND is_recurring = true AND EXTRACT(MONTH FROM bill_date) = EXTRACT(MONTH FROM tanggal_bisnis(p_tenant_id))
            AND EXTRACT(YEAR FROM bill_date) = EXTRACT(YEAR FROM tanggal_bisnis(p_tenant_id))),
        (SELECT COUNT(*)::INTEGER FROM recurring_bills WHERE tenant_id = p_tenant_id
            AND status = 'active' AND next_bill_date = tanggal_bisnis(p_tenant_id)),
        (SELECT COUNT(*)::INTEGER FROM recurring_bills WHERE tenant_id = p_tenant_id
            AND status = 'active' AND next_bill_date BETWEEN tanggal_bisnis(p_tenant_id) AND tanggal_bisnis(p_tenant_id) + INTERVAL '7 days');
END;
$function$
;

-- get_upcoming_cheques: 3x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.get_upcoming_cheques(p_tenant_id text, p_days integer DEFAULT 30, p_cheque_type character varying DEFAULT NULL::character varying)
 RETURNS TABLE(id uuid, cheque_number character varying, cheque_date date, cheque_type character varying, amount bigint, party_name character varying, days_until_due integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.cheque_number,
        c.cheque_date,
        c.cheque_type,
        c.amount,
        c.party_name,
        (c.cheque_date - tanggal_bisnis(p_tenant_id))::INTEGER as days_until_due
    FROM cheques c
    WHERE c.tenant_id = p_tenant_id
    AND c.status = 'pending'
    AND (p_cheque_type IS NULL OR c.cheque_type = p_cheque_type)
    AND c.cheque_date BETWEEN tanggal_bisnis(p_tenant_id) AND (tanggal_bisnis(p_tenant_id) + p_days)
    ORDER BY c.cheque_date ASC;
END;
$function$
;

-- mark_serials_sold: 1x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.mark_serials_sold(p_tenant_id text, p_serial_ids uuid[], p_sales_invoice_id uuid DEFAULT NULL::uuid, p_sales_receipt_id uuid DEFAULT NULL::uuid, p_customer_id uuid DEFAULT NULL::uuid, p_sold_by uuid DEFAULT NULL::uuid)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_serial_id UUID;
    v_count INT := 0;
    v_ref_type VARCHAR;
    v_ref_id UUID;
BEGIN
    IF p_sales_invoice_id IS NOT NULL THEN
        v_ref_type := 'sales_invoice';
        v_ref_id := p_sales_invoice_id;
    ELSE
        v_ref_type := 'sales_receipt';
        v_ref_id := p_sales_receipt_id;
    END IF;

    FOREACH v_serial_id IN ARRAY p_serial_ids
    LOOP
        -- Record movement
        PERFORM record_serial_movement(
            p_tenant_id, v_serial_id, 'sold',
            NULL, 'sold',
            v_ref_type, v_ref_id, NULL,
            p_sold_by, NULL
        );

        -- Update serial with sale info
        UPDATE item_serials
        SET sales_invoice_id = p_sales_invoice_id,
            sales_receipt_id = p_sales_receipt_id,
            customer_id = p_customer_id,
            sold_date = tanggal_bisnis(p_tenant_id),
            updated_at = NOW()
        WHERE id = v_serial_id AND tenant_id = p_tenant_id;

        v_count := v_count + 1;
    END LOOP;

    RETURN v_count;
END;
$function$
;

-- process_expired_batches: 1x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.process_expired_batches()
 RETURNS TABLE(tenant_id text, batches_expired integer)
 LANGUAGE plpgsql
AS $function$
DECLARE
    r RECORD;
    v_count INT;
BEGIN
    FOR r IN SELECT DISTINCT ib.tenant_id FROM item_batches ib
    LOOP
        UPDATE item_batches
        SET status = 'expired', updated_at = NOW()
        WHERE item_batches.tenant_id = r.tenant_id
        AND status = 'active'
        AND expiry_date IS NOT NULL
        AND expiry_date < tanggal_bisnis(r.tenant_id);

        GET DIAGNOSTICS v_count = ROW_COUNT;

        tenant_id := r.tenant_id;
        batches_expired := v_count;
        RETURN NEXT;
    END LOOP;
END;
$function$
;

-- receive_stock_transfer: 2x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.receive_stock_transfer(p_transfer_id uuid, p_received_by uuid, p_items jsonb DEFAULT NULL::jsonb)
 RETURNS TABLE(success boolean, message text)
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_transfer RECORD;
    v_item RECORD;
    v_tenant_id TEXT;
    v_received_qty DECIMAL;
BEGIN
    -- Get transfer
    SELECT * INTO v_transfer
    FROM stock_transfers
    WHERE id = p_transfer_id FOR UPDATE;

    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 'Transfer not found'::TEXT;
        RETURN;
    END IF;

    v_tenant_id := v_transfer.tenant_id;

    IF v_transfer.status != 'in_transit' THEN
        RETURN QUERY SELECT false, ('Cannot receive transfer with status: ' || v_transfer.status)::TEXT;
        RETURN;
    END IF;

    -- Add stock to destination warehouse
    FOR v_item IN
        SELECT * FROM stock_transfer_items WHERE stock_transfer_id = p_transfer_id
    LOOP
        -- Determine received quantity
        IF p_items IS NOT NULL THEN
            SELECT (elem->>'quantity_received')::DECIMAL INTO v_received_qty
            FROM jsonb_array_elements(p_items) elem
            WHERE (elem->>'item_id')::UUID = v_item.item_id;

            IF v_received_qty IS NULL THEN
                v_received_qty := v_item.quantity_shipped;
            END IF;
        ELSE
            v_received_qty := v_item.quantity_shipped;
        END IF;

        -- Insert positive quantity to inventory_ledger
        INSERT INTO inventory_ledger (
            tenant_id, item_id, warehouse_id,
            quantity_change, unit_cost, total_value,
            source_type, source_id,
            transaction_date, created_at
        ) VALUES (
            v_tenant_id, v_item.item_id, v_transfer.to_warehouse_id,
            v_received_qty, v_item.unit_cost, (v_received_qty * v_item.unit_cost),
            'STOCK_TRANSFER_IN', p_transfer_id,
            tanggal_bisnis(v_tenant_id), NOW()
        );

        -- Update received quantity
        UPDATE stock_transfer_items
        SET quantity_received = v_received_qty
        WHERE id = v_item.id;
    END LOOP;

    -- Update transfer status
    UPDATE stock_transfers
    SET status = 'received',
        received_date = tanggal_bisnis(v_tenant_id),
        received_by = p_received_by,
        updated_at = NOW()
    WHERE id = p_transfer_id;

    RETURN QUERY SELECT true, 'Transfer received successfully'::TEXT;
END;
$function$
;

-- ship_stock_transfer: 1x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.ship_stock_transfer(p_transfer_id uuid, p_shipped_by uuid)
 RETURNS TABLE(success boolean, message text)
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_transfer RECORD;
    v_item RECORD;
    v_tenant_id TEXT;
    v_available DECIMAL;
BEGIN
    -- Get transfer
    SELECT * INTO v_transfer
    FROM stock_transfers
    WHERE id = p_transfer_id FOR UPDATE;

    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 'Transfer not found'::TEXT;
        RETURN;
    END IF;

    v_tenant_id := v_transfer.tenant_id;

    IF v_transfer.status != 'draft' THEN
        RETURN QUERY SELECT false, ('Cannot ship transfer with status: ' || v_transfer.status)::TEXT;
        RETURN;
    END IF;

    -- Check stock availability for each item
    FOR v_item IN
        SELECT * FROM stock_transfer_items WHERE stock_transfer_id = p_transfer_id
    LOOP
        v_available := get_available_stock(v_tenant_id, v_transfer.from_warehouse_id, v_item.item_id);

        IF v_available < v_item.quantity_requested THEN
            RETURN QUERY SELECT false,
                ('Insufficient stock for ' || v_item.item_name || ': available=' || v_available || ', requested=' || v_item.quantity_requested)::TEXT;
            RETURN;
        END IF;
    END LOOP;

    -- Reduce stock from source warehouse via inventory_ledger
    FOR v_item IN
        SELECT * FROM stock_transfer_items WHERE stock_transfer_id = p_transfer_id
    LOOP
        -- Insert negative quantity to inventory_ledger
        INSERT INTO inventory_ledger (
            tenant_id, item_id, warehouse_id,
            quantity_change, unit_cost, total_value,
            source_type, source_id,
            transaction_date, created_at
        ) VALUES (
            v_tenant_id, v_item.item_id, v_transfer.from_warehouse_id,
            -v_item.quantity_requested, v_item.unit_cost, -(v_item.quantity_requested * v_item.unit_cost),
            'STOCK_TRANSFER_OUT', p_transfer_id,
            v_transfer.transfer_date, NOW()
        );

        -- Update shipped quantity
        UPDATE stock_transfer_items
        SET quantity_shipped = v_item.quantity_requested
        WHERE id = v_item.id;
    END LOOP;

    -- Update transfer status
    UPDATE stock_transfers
    SET status = 'in_transit',
        shipped_date = tanggal_bisnis(v_tenant_id),
        shipped_by = p_shipped_by,
        updated_at = NOW()
    WHERE id = p_transfer_id;

    RETURN QUERY SELECT true, 'Transfer shipped successfully'::TEXT;
END;
$function$
;

-- update_sales_invoice_status: 1x CURRENT_DATE
CREATE OR REPLACE FUNCTION public.update_sales_invoice_status()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_total_paid BIGINT;
    v_total_amount BIGINT;
    v_due_date DATE;
    v_new_status VARCHAR(20);
BEGIN
    -- Get invoice details
    SELECT total_amount, due_date INTO v_total_amount, v_due_date
    FROM sales_invoices
    WHERE id = NEW.invoice_id;

    -- Calculate total paid
    SELECT COALESCE(SUM(amount), 0) INTO v_total_paid
    FROM sales_invoice_payments
    WHERE invoice_id = NEW.invoice_id;

    -- Determine new status
    IF v_total_paid >= v_total_amount THEN
        v_new_status := 'paid';
    ELSIF v_total_paid > 0 THEN
        v_new_status := 'partial';
    ELSIF v_due_date < tanggal_bisnis((SELECT si.tenant_id FROM sales_invoices si WHERE si.id = NEW.invoice_id)) THEN
        v_new_status := 'overdue';
    ELSE
        v_new_status := 'posted';
    END IF;

    -- Update invoice
    UPDATE sales_invoices
    SET amount_paid = v_total_paid,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = NEW.invoice_id
      AND status NOT IN ('draft', 'void');

    RETURN NEW;
END;
$function$
;
