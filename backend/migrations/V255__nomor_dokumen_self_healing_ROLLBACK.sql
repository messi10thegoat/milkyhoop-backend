-- ROLLBACK V255 — kembalikan 3 generator ke versi non-self-healing (hanya naikkan counter).
CREATE OR REPLACE FUNCTION public.generate_sales_invoice_number(
    p_tenant_id text, p_prefix character varying DEFAULT 'INV'::character varying)
RETURNS character varying LANGUAGE plpgsql AS $function$
DECLARE v_year_month VARCHAR(7); v_next_number INT; v_invoice_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');
    INSERT INTO sales_invoice_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = sales_invoice_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;
    v_invoice_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');
    RETURN v_invoice_number;
END; $function$;

CREATE OR REPLACE FUNCTION public.generate_quote_number(
    p_tenant_id text, p_prefix character varying DEFAULT 'QUO'::character varying)
RETURNS character varying LANGUAGE plpgsql AS $function$
DECLARE v_year_month VARCHAR(7); v_next_number INT; v_quote_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');
    INSERT INTO quote_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = quote_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;
    v_quote_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');
    RETURN v_quote_number;
END; $function$;

CREATE OR REPLACE FUNCTION public.generate_sales_order_number(
    p_tenant_id text, p_prefix character varying DEFAULT 'SO'::character varying)
RETURNS character varying LANGUAGE plpgsql AS $function$
DECLARE v_year_month VARCHAR(7); v_next_number INT; v_order_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');
    INSERT INTO sales_order_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = sales_order_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;
    v_order_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');
    RETURN v_order_number;
END; $function$;
