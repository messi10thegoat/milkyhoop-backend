-- V255 (14 Sep 2026) — Generator nomor dokumen SELF-HEALING (faktur/penawaran/pesanan).
-- Latar: nomor boleh diisi manual saat membuat (unik/tenant lewat index). Generator lama hanya
-- menaikkan counter TANPA cek keberadaan -> kandidat bisa bentrok dgn nomor MANUAL -> UniqueViolation.
-- Perbaikan: loop berbatas — sesudah hitung kandidat, kalau sudah dipakai di tabel dokumen untuk
-- tenant itu, naikkan counter lagi (JANGAN mundur) dan ulang. Pola journal-number self-healing.
-- Signature & format TAK berubah (INV/QUO/SO-YYMM-NNNN); jalur auto byte-identik saat tak ada manual.

CREATE OR REPLACE FUNCTION public.generate_sales_invoice_number(
    p_tenant_id text, p_prefix character varying DEFAULT 'INV'::character varying)
RETURNS character varying LANGUAGE plpgsql AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_invoice_number VARCHAR(50);
    v_tries INT := 0;
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');
    LOOP
        INSERT INTO sales_invoice_sequences (tenant_id, year_month, last_number, prefix)
        VALUES (p_tenant_id, v_year_month, 1, p_prefix)
        ON CONFLICT (tenant_id, year_month)
        DO UPDATE SET last_number = sales_invoice_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number INTO v_next_number;

        v_invoice_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

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

CREATE OR REPLACE FUNCTION public.generate_quote_number(
    p_tenant_id text, p_prefix character varying DEFAULT 'QUO'::character varying)
RETURNS character varying LANGUAGE plpgsql AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_quote_number VARCHAR(50);
    v_tries INT := 0;
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');
    LOOP
        INSERT INTO quote_sequences (tenant_id, year_month, last_number, prefix)
        VALUES (p_tenant_id, v_year_month, 1, p_prefix)
        ON CONFLICT (tenant_id, year_month)
        DO UPDATE SET last_number = quote_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number INTO v_next_number;

        v_quote_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

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

CREATE OR REPLACE FUNCTION public.generate_sales_order_number(
    p_tenant_id text, p_prefix character varying DEFAULT 'SO'::character varying)
RETURNS character varying LANGUAGE plpgsql AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_order_number VARCHAR(50);
    v_tries INT := 0;
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');
    LOOP
        INSERT INTO sales_order_sequences (tenant_id, year_month, last_number, prefix)
        VALUES (p_tenant_id, v_year_month, 1, p_prefix)
        ON CONFLICT (tenant_id, year_month)
        DO UPDATE SET last_number = sales_order_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number INTO v_next_number;

        v_order_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

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
