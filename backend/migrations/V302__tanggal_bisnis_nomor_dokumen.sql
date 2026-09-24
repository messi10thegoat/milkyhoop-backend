-- V302 (#10c, 25 Sep 2026): NOMOR dokumen & jurnal ikut TANGGAL BISNIS tenant, bukan tanggal UTC server.
-- Postgres berjalan UTC: CURRENT_DATE di 25 fungsi generate_*_number + default get_next_journal_number
-- = tanggal UTC -> dokumen 1 Okt 00:30 WIB bernomor INV-2609-…, urutan bulanan ikut UTC.
-- Satu sumber: tanggal_bisnis(tenant, p_now) = cermin app/utils/tanggal_tenant.py
-- ("Tenant".timezone, cadangan Asia/Jakarta, zona tak dikenal -> cadangan). p_now memungkinkan gerbang
-- menyuntik jam. updated_at/exhausted_at (cap waktu) TETAP NOW(). generate_efaktur_number tak disentuh
-- (NOW() hanya cap waktu). Badan fungsi = pg_get_functiondef prod 25 Sep, hanya CURRENT_DATE /
-- TO_CHAR(NOW(),…) -> tanggal_bisnis(p_tenant_id); get_next_journal_number: default p_date NULL ->
-- COALESCE(p_date, tanggal_bisnis(p_tenant_id)) (default param tak boleh merujuk param lain).

CREATE OR REPLACE FUNCTION public.tanggal_bisnis(p_tenant_id text, p_now timestamptz DEFAULT now())
 RETURNS date
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE
    v_tz text;
BEGIN
    SELECT NULLIF(btrim(timezone), '') INTO v_tz FROM "Tenant" WHERE id = p_tenant_id;
    BEGIN
        RETURN (p_now AT TIME ZONE COALESCE(v_tz, 'Asia/Jakarta'))::date;
    EXCEPTION WHEN invalid_parameter_value THEN
        RETURN (p_now AT TIME ZONE 'Asia/Jakarta')::date;
    END;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_asset_number(p_tenant_id text)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_number INTEGER;
    v_year TEXT;
BEGIN
    v_year := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY');

    INSERT INTO fixed_asset_sequences (tenant_id, last_number)
    VALUES (p_tenant_id, 1)
    ON CONFLICT (tenant_id)
    DO UPDATE SET last_number = fixed_asset_sequences.last_number + 1
    RETURNING last_number INTO v_number;

    RETURN 'FA-' || v_year || '-' || LPAD(v_number::TEXT, 5, '0');
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_bank_transfer_number(p_tenant_id text, p_prefix character varying DEFAULT 'TRF'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_transfer_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO bank_transfer_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = bank_transfer_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: TRF-YYMM-0001
    v_transfer_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_transfer_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_bill_number(p_tenant_id text, p_prefix character varying DEFAULT 'BILL'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_bill_number VARCHAR(50);
BEGIN
    -- Get current year-month
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    -- Insert or update sequence (atomic)
    INSERT INTO bill_number_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET
        last_number = bill_number_sequences.last_number + 1,
        updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: BILL-2501-0001
    v_bill_number := p_prefix || '-' ||
                     SUBSTRING(v_year_month, 3, 2) ||
                     SUBSTRING(v_year_month, 6, 2) || '-' ||
                     LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_bill_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_branch_transfer_number(p_tenant_id text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_prefix VARCHAR(10);
    v_number INTEGER;
    v_year INTEGER;
BEGIN
    v_year := EXTRACT(YEAR FROM tanggal_bisnis(p_tenant_id));

    INSERT INTO branch_transfer_sequences (tenant_id, last_number, prefix, last_reset_year)
    VALUES (p_tenant_id, 1, 'BT', v_year)
    ON CONFLICT (tenant_id) DO UPDATE SET
        last_number = CASE
            WHEN branch_transfer_sequences.last_reset_year != v_year THEN 1
            ELSE branch_transfer_sequences.last_number + 1
        END,
        last_reset_year = v_year
    RETURNING prefix, last_number INTO v_prefix, v_number;

    RETURN v_prefix || '-' || v_year || '-' || LPAD(v_number::TEXT, 6, '0');
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_credit_note_number(p_tenant_id text, p_prefix character varying DEFAULT 'CN'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_cn_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO credit_note_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = credit_note_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    v_cn_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_cn_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_customer_deposit_number(p_tenant_id text, p_prefix character varying DEFAULT 'DEP'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_deposit_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO customer_deposit_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = customer_deposit_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: DEP-YYMM-0001
    v_deposit_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_deposit_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_expense_number(p_tenant_id text)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_prefix VARCHAR(10);
    v_number INT;
    v_year_month VARCHAR(4);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM');

    -- Insert or update sequence (atomic)
    INSERT INTO expense_sequences (tenant_id, prefix, last_number, last_reset_date)
    VALUES (p_tenant_id, 'EXP', 1, tanggal_bisnis(p_tenant_id))
    ON CONFLICT (tenant_id) DO UPDATE
    SET last_number = CASE
        WHEN TO_CHAR(expense_sequences.last_reset_date, 'YYMM') != v_year_month
        THEN 1
        ELSE expense_sequences.last_number + 1
    END,
    last_reset_date = tanggal_bisnis(p_tenant_id)
    RETURNING prefix, last_number INTO v_prefix, v_number;

    -- Format: EXP-2601-0001
    RETURN v_prefix || '-' || v_year_month || '-' || LPAD(v_number::TEXT, 4, '0');
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_ic_transaction_number(p_tenant_id text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_prefix VARCHAR(10);
    v_number INTEGER;
    v_year INTEGER;
BEGIN
    v_year := EXTRACT(YEAR FROM tanggal_bisnis(p_tenant_id));

    INSERT INTO intercompany_sequences (tenant_id, last_number, prefix, last_reset_year)
    VALUES (p_tenant_id, 1, 'IC', v_year)
    ON CONFLICT (tenant_id) DO UPDATE SET
        last_number = CASE
            WHEN intercompany_sequences.last_reset_year != v_year THEN 1
            ELSE intercompany_sequences.last_number + 1
        END,
        last_reset_year = v_year
    RETURNING prefix, last_number INTO v_prefix, v_number;

    RETURN v_prefix || '-' || v_year || '-' || LPAD(v_number::TEXT, 6, '0');
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_payment_request_number(p_tenant_id text)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year TEXT;
    v_seq INT;
    v_number VARCHAR(50);
BEGIN
    v_year := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY');
    SELECT COALESCE(MAX(
        CAST(SUBSTRING(request_number FROM 'PR-' || v_year || '-(\d+)') AS INT)
    ), 0) + 1
    INTO v_seq
    FROM payment_requests
    WHERE tenant_id = p_tenant_id
    AND request_number LIKE 'PR-' || v_year || '-%';
    v_number := 'PR-' || v_year || '-' || LPAD(v_seq::TEXT, 4, '0');
    RETURN v_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_production_order_number(p_tenant_id text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_prefix VARCHAR(10);
    v_number INTEGER;
    v_year INTEGER;
BEGIN
    v_year := EXTRACT(YEAR FROM tanggal_bisnis(p_tenant_id));

    INSERT INTO production_sequences (tenant_id, last_number, prefix, last_reset_year)
    VALUES (p_tenant_id, 1, 'WO', v_year)
    ON CONFLICT (tenant_id) DO UPDATE SET
        last_number = CASE
            WHEN production_sequences.last_reset_year != v_year THEN 1
            ELSE production_sequences.last_number + 1
        END,
        last_reset_year = v_year
    RETURNING prefix, last_number INTO v_prefix, v_number;

    RETURN v_prefix || '-' || v_year || '-' || LPAD(v_number::TEXT, 6, '0');
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_proforma_number(p_tenant_id text, p_prefix character varying DEFAULT 'PRO'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_proforma_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO proforma_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = proforma_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: PRO-YYMM-0001
    v_proforma_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_proforma_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_purchase_bill_number(p_tenant_id text)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_bill_number VARCHAR(50);
BEGIN
    -- Get current year-month
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    -- Insert or update sequence (atomic)
    INSERT INTO bill_number_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, 'PB')
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET
        last_number = bill_number_sequences.last_number + 1,
        updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: PB-2601-0001
    v_bill_number := 'PB-' ||
                     SUBSTRING(v_year_month, 3, 2) ||
                     SUBSTRING(v_year_month, 6, 2) || '-' ||
                     LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_bill_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_purchase_order_number(p_tenant_id text, p_prefix character varying DEFAULT 'PO'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_po_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO purchase_order_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = purchase_order_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: PO-YYMM-0001
    v_po_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_po_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_quote_number(p_tenant_id text, p_prefix character varying DEFAULT 'QUO'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_quote_number VARCHAR(50);
    v_tries INT := 0;
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');
    LOOP
        INSERT INTO quote_sequences (tenant_id, year_month, last_number, prefix)
        VALUES (p_tenant_id, v_year_month, 1, p_prefix)
        ON CONFLICT (tenant_id, year_month)
        DO UPDATE SET last_number = quote_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number INTO v_next_number;

        v_quote_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

        EXIT WHEN NOT EXISTS (
            SELECT 1 FROM quotes WHERE tenant_id = p_tenant_id AND quote_number = v_quote_number);
        v_tries := v_tries + 1;
        IF v_tries > 100000 THEN
            RAISE EXCEPTION 'generate_quote_number: gagal menemukan nomor bebas (% percobaan)', v_tries;
        END IF;
    END LOOP;
    RETURN v_quote_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_receive_payment_number(p_tenant_id text)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year INT;
    v_next_number INT;
    v_payment_number VARCHAR(50);
BEGIN
    v_year := EXTRACT(YEAR FROM tanggal_bisnis(p_tenant_id));

    INSERT INTO receive_payment_sequences (tenant_id, last_number, year)
    VALUES (p_tenant_id, 1, v_year)
    ON CONFLICT (tenant_id)
    DO UPDATE SET
        last_number = CASE
            WHEN receive_payment_sequences.year = v_year
            THEN receive_payment_sequences.last_number + 1
            ELSE 1
        END,
        year = v_year
    RETURNING last_number INTO v_next_number;

    -- Format: RCV-YYYY-NNNN
    v_payment_number := 'RCV-' || v_year || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_payment_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_reconciliation_number(p_tenant_id text, p_prefix character varying DEFAULT 'REC'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_recon_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO bank_reconciliation_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = bank_reconciliation_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    v_recon_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_recon_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_reservation_number(p_tenant_id text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_prefix VARCHAR(10);
    v_number INTEGER;
    v_year INTEGER;
BEGIN
    v_year := EXTRACT(YEAR FROM tanggal_bisnis(p_tenant_id));

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
$function$;

CREATE OR REPLACE FUNCTION public.generate_sales_invoice_number(p_tenant_id text, p_prefix character varying DEFAULT 'INV'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_invoice_number VARCHAR(50);
    v_tries INT := 0;
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');
    LOOP
        INSERT INTO sales_invoice_sequences (tenant_id, year_month, last_number, prefix)
        VALUES (p_tenant_id, v_year_month, 1, p_prefix)
        ON CONFLICT (tenant_id, year_month)
        DO UPDATE SET last_number = sales_invoice_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number INTO v_next_number;

        v_invoice_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

        EXIT WHEN NOT EXISTS (
            SELECT 1 FROM sales_invoices WHERE tenant_id = p_tenant_id AND invoice_number = v_invoice_number);
        v_tries := v_tries + 1;
        IF v_tries > 100000 THEN
            RAISE EXCEPTION 'generate_sales_invoice_number: gagal menemukan nomor bebas (% percobaan)', v_tries;
        END IF;
    END LOOP;
    RETURN v_invoice_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_sales_order_number(p_tenant_id text, p_prefix character varying DEFAULT 'SO'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_order_number VARCHAR(50);
    v_tries INT := 0;
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');
    LOOP
        INSERT INTO sales_order_sequences (tenant_id, year_month, last_number, prefix)
        VALUES (p_tenant_id, v_year_month, 1, p_prefix)
        ON CONFLICT (tenant_id, year_month)
        DO UPDATE SET last_number = sales_order_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number INTO v_next_number;

        v_order_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

        EXIT WHEN NOT EXISTS (
            SELECT 1 FROM sales_orders WHERE tenant_id = p_tenant_id AND order_number = v_order_number);
        v_tries := v_tries + 1;
        IF v_tries > 100000 THEN
            RAISE EXCEPTION 'generate_sales_order_number: gagal menemukan nomor bebas (% percobaan)', v_tries;
        END IF;
    END LOOP;
    RETURN v_order_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_sales_receipt_number(p_tenant_id text, p_prefix character varying DEFAULT 'SR'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_sr_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO sales_receipt_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = sales_receipt_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    v_sr_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_sr_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_shipment_number(p_tenant_id text, p_prefix character varying DEFAULT 'SHP'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_shipment_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO shipment_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = shipment_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: SHP-YYMM-0001
    v_shipment_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_shipment_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_stock_adjustment_number(p_tenant_id text, p_prefix character varying DEFAULT 'SA'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_sa_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO stock_adjustment_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = stock_adjustment_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    v_sa_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_sa_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_stock_transfer_number(p_tenant_id text, p_prefix character varying DEFAULT 'ST'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_st_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO stock_transfer_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = stock_transfer_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    v_st_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_st_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_vendor_credit_number(p_tenant_id text, p_prefix character varying DEFAULT 'VC'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_vc_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');

    INSERT INTO vendor_credit_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = vendor_credit_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    v_vc_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_vc_number;
END;
$function$;

CREATE OR REPLACE FUNCTION public.generate_vendor_deposit_number(p_tenant_id text)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_number INTEGER;
    v_year TEXT;
BEGIN
    v_year := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY');

    INSERT INTO vendor_deposit_sequences (tenant_id, last_number)
    VALUES (p_tenant_id, 1)
    ON CONFLICT (tenant_id)
    DO UPDATE SET last_number = vendor_deposit_sequences.last_number + 1
    RETURNING last_number INTO v_number;

    RETURN 'VD-' || v_year || '-' || LPAD(v_number::TEXT, 5, '0');
END;
$function$;

CREATE OR REPLACE FUNCTION public.get_next_journal_number(p_tenant_id text, p_prefix text DEFAULT 'JV'::text, p_date date DEFAULT NULL::date)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year         INTEGER := EXTRACT(YEAR  FROM COALESCE(p_date, tanggal_bisnis(p_tenant_id)))::INTEGER;
    v_month        INTEGER := EXTRACT(MONTH FROM COALESCE(p_date, tanggal_bisnis(p_tenant_id)))::INTEGER;
    v_yymm         TEXT    := to_char(COALESCE(p_date, tanggal_bisnis(p_tenant_id)), 'YYMM');
    v_actual_max   INTEGER;
    v_number       INTEGER;
BEGIN
    -- Highest numeric suffix already emitted for this tenant/prefix/yymm.
    -- Anchored pattern so 'JV' does not match 'JV-LB-...' etc.
    SELECT COALESCE(MAX((regexp_match(journal_number, '^' || p_prefix || '-' || v_yymm || '-([0-9]+)$'))[1]::int), 0)
      INTO v_actual_max
      FROM journal_entries
     WHERE tenant_id = p_tenant_id
       AND journal_number ~ ('^' || p_prefix || '-' || v_yymm || '-[0-9]+$');

    -- Upsert + self-heal: counter advances monotonically AND never falls behind
    -- the actual emitted max.
    INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number)
    VALUES (p_tenant_id, p_prefix, v_year, v_month, GREATEST(1, v_actual_max + 1))
    ON CONFLICT (tenant_id, prefix, year, month)
    DO UPDATE SET
        last_number = GREATEST(journal_number_sequences.last_number + 1, v_actual_max + 1),
        updated_at  = NOW()
    RETURNING last_number INTO v_number;

    RETURN p_prefix || '-' || v_yymm || '-' || lpad(v_number::TEXT, 4, '0');
END;
$function$;
