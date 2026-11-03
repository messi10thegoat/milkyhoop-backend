-- V475 (3 Nov 2026, CONVERSATIONAL; putusan MASTER, repro BACKEND2 di PG sekali-pakai master 5958ee069): penghitung nomor
-- PEMBELIAN melompati nomor yang SUDAH dipakai -- pola generate_sales_order_number (V302:494-523) PERSIS.
--
-- Cacat: generate_purchase_bill_number / generate_bill_number / generate_purchase_order_number hanya +1. Nomor manual yang sama
-- dengan nomor otomatis berikutnya (mis. "PB-2611-0002") -> create tanpa nomor berikutnya bentrok UNIQUE -> rollback ikut
-- mengembalikan last_number -> SETIAP create otomatis sesudahnya bentrok lagi (400 terus, penghitung tak pernah maju).
-- Perbaikan: LOOP naikkan penghitung sampai nomor bebas di tabel dokumennya (per tenant; indeks UNIQUE (tenant_id, nomor) ada:
-- uq_bills_tenant_invoice V018, uq_po_tenant_number V037), batas 100000 percobaan -> galat berkalimat.
-- HANYA fungsi (CREATE OR REPLACE); nol perubahan data/skema; format nomor SAMA. bill_number_sequences tetap dipakai bersama
-- seri BILL- dan PB- (kunci tenant+bulan, keadaan lama -- tak diubah di sini).

CREATE OR REPLACE FUNCTION public.generate_bill_number(p_tenant_id text, p_prefix character varying DEFAULT 'BILL'::character varying)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_bill_number VARCHAR(50);
    v_tries INT := 0;
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');
    LOOP
        INSERT INTO bill_number_sequences (tenant_id, year_month, last_number, prefix)
        VALUES (p_tenant_id, v_year_month, 1, p_prefix)
        ON CONFLICT (tenant_id, year_month)
        DO UPDATE SET last_number = bill_number_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number INTO v_next_number;

        -- Format: BILL-2501-0001
        v_bill_number := p_prefix || '-' || SUBSTRING(v_year_month, 3, 2) || SUBSTRING(v_year_month, 6, 2) || '-' ||
                         LPAD(v_next_number::TEXT, 4, '0');

        EXIT WHEN NOT EXISTS (SELECT 1 FROM bills WHERE tenant_id = p_tenant_id AND invoice_number = v_bill_number);
        v_tries := v_tries + 1;
        IF v_tries > 100000 THEN
            RAISE EXCEPTION 'generate_bill_number: gagal menemukan nomor bebas (% percobaan)', v_tries;
        END IF;
    END LOOP;
    RETURN v_bill_number;
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
    v_tries INT := 0;
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');
    LOOP
        INSERT INTO bill_number_sequences (tenant_id, year_month, last_number, prefix)
        VALUES (p_tenant_id, v_year_month, 1, 'PB')
        ON CONFLICT (tenant_id, year_month)
        DO UPDATE SET last_number = bill_number_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number INTO v_next_number;

        -- Format: PB-2601-0001
        v_bill_number := 'PB-' || SUBSTRING(v_year_month, 3, 2) || SUBSTRING(v_year_month, 6, 2) || '-' ||
                         LPAD(v_next_number::TEXT, 4, '0');

        EXIT WHEN NOT EXISTS (SELECT 1 FROM bills WHERE tenant_id = p_tenant_id AND invoice_number = v_bill_number);
        v_tries := v_tries + 1;
        IF v_tries > 100000 THEN
            RAISE EXCEPTION 'generate_purchase_bill_number: gagal menemukan nomor bebas (% percobaan)', v_tries;
        END IF;
    END LOOP;
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
    v_tries INT := 0;
BEGIN
    v_year_month := TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM');
    LOOP
        INSERT INTO purchase_order_sequences (tenant_id, year_month, last_number, prefix)
        VALUES (p_tenant_id, v_year_month, 1, p_prefix)
        ON CONFLICT (tenant_id, year_month)
        DO UPDATE SET last_number = purchase_order_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number INTO v_next_number;

        -- Format: PO-YYMM-0001
        v_po_number := p_prefix || '-' || TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

        EXIT WHEN NOT EXISTS (SELECT 1 FROM purchase_orders WHERE tenant_id = p_tenant_id AND po_number = v_po_number);
        v_tries := v_tries + 1;
        IF v_tries > 100000 THEN
            RAISE EXCEPTION 'generate_purchase_order_number: gagal menemukan nomor bebas (% percobaan)', v_tries;
        END IF;
    END LOOP;
    RETURN v_po_number;
END;
$function$;
