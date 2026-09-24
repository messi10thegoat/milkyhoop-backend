-- Gerbang #10c: nomor dokumen/jurnal ikut TANGGAL BISNIS tenant (V302). Jalankan HANYA di DB scratch
-- (skema milkydb + baris "Tenant"): skrip ini memanggil 26 fungsi penomoran (menaikkan urutan) dan
-- MENGGANTI sementara tanggal_bisnis -- semuanya dalam SATU transaksi yang di-ROLLBACK.
-- Keluar != 0 bila ada yang gagal (ON_ERROR_STOP + RAISE EXCEPTION).
\set ON_ERROR_STOP on
BEGIN;

-- A. Penjaga statis: badan 26 fungsi tak lagi memakai tanggal server.
DO $$
DECLARE n int; daftar text;
BEGIN
  SELECT count(*), string_agg(proname, ', ') INTO n, daftar FROM pg_proc
  WHERE (proname LIKE 'generate\_%number%' AND proname <> 'generate_efaktur_number' OR proname = 'get_next_journal_number')
    AND (prosrc ~* 'CURRENT_DATE|TO_CHAR\(\s*NOW\(\)' OR pg_get_function_arguments(oid) ~* 'DEFAULT CURRENT_DATE');
  IF n > 0 THEN RAISE EXCEPTION 'GAGAL A: % fungsi masih bertanggal server: %', n, daftar; END IF;
  RAISE NOTICE 'LULUS A: 0 fungsi penomoran memakai CURRENT_DATE/NOW()';
END $$;

-- B. tanggal_bisnis sendiri (p_now eksplisit).
DO $$
DECLARE t text;
BEGIN
  SELECT id INTO t FROM "Tenant" WHERE alias = 'kaos-biru-konveksi';
  IF tanggal_bisnis(t, '2026-09-30 18:00+00') <> DATE '2026-10-01' THEN RAISE EXCEPTION 'GAGAL B1 18:00Z'; END IF;
  IF tanggal_bisnis(t, '2026-09-30 10:00+00') <> DATE '2026-09-30' THEN RAISE EXCEPTION 'GAGAL B2 10:00Z'; END IF;
  IF tanggal_bisnis('tenant-tak-ada', '2026-09-30 18:00+00') <> DATE '2026-10-01' THEN RAISE EXCEPTION 'GAGAL B3 cadangan'; END IF;
  UPDATE "Tenant" SET timezone = 'Planet/Mars' WHERE id = t;           -- zona tak dikenal -> cadangan
  IF tanggal_bisnis(t, '2026-09-30 18:00+00') <> DATE '2026-10-01' THEN RAISE EXCEPTION 'GAGAL B4 zona rusak'; END IF;
  UPDATE "Tenant" SET timezone = 'Asia/Makassar' WHERE id = t;         -- WITA ikut zona tenant
  IF tanggal_bisnis(t, '2026-09-30 16:30+00') <> DATE '2026-10-01' THEN RAISE EXCEPTION 'GAGAL B5 WITA'; END IF;
  UPDATE "Tenant" SET timezone = 'Asia/Jakarta' WHERE id = t;
  RAISE NOTICE 'LULUS B: tanggal_bisnis 18:00Z->1 Okt, 10:00Z->30 Sep, cadangan, zona rusak, WITA';
END $$;

-- C. Jam DISUNTIK lewat default p_now; tiap fungsi penomoran harus mengikuti.
CREATE OR REPLACE FUNCTION pg_temp.uji_nomor(p_jam timestamptz, p_tahunan boolean, p_harap text)
RETURNS int LANGUAGE plpgsql AS $f$
DECLARE t text; f text; hasil text; gagal int := 0;
  bulanan text[] := ARRAY['generate_bank_transfer_number($1)','generate_bill_number($1)',
    'generate_credit_note_number($1)','generate_customer_deposit_number($1)','generate_expense_number($1)',
    'generate_proforma_number($1)','generate_purchase_bill_number($1)','generate_purchase_order_number($1)',
    'generate_quote_number($1)','generate_reconciliation_number($1)','generate_sales_invoice_number($1)',
    'generate_sales_order_number($1)','generate_sales_receipt_number($1)','generate_shipment_number($1)',
    'generate_stock_adjustment_number($1)','generate_stock_transfer_number($1)',
    'generate_vendor_credit_number($1)','get_next_journal_number($1, ''JV'')'];
  tahunan text[] := ARRAY['generate_asset_number($1)','generate_branch_transfer_number($1)',
    'generate_ic_transaction_number($1)','generate_payment_request_number($1)',
    'generate_production_order_number($1)','generate_receive_payment_number($1)',
    'generate_reservation_number($1)','generate_vendor_deposit_number($1)'];
BEGIN
  SELECT id INTO t FROM "Tenant" WHERE alias = 'kaos-biru-konveksi';
  EXECUTE format($d$CREATE OR REPLACE FUNCTION public.tanggal_bisnis(p_tenant_id text, p_now timestamptz DEFAULT %L)
    RETURNS date LANGUAGE plpgsql STABLE AS $b$ BEGIN RETURN (p_now AT TIME ZONE 'Asia/Jakarta')::date; END $b$$d$, p_jam);
  FOREACH f IN ARRAY CASE WHEN p_tahunan THEN tahunan ELSE bulanan END LOOP
    EXECUTE 'SELECT ' || f INTO hasil USING t;
    IF position(p_harap IN hasil) = 0 THEN
      gagal := gagal + 1; RAISE NOTICE 'GAGAL C % @% -> % (harap memuat %)', f, p_jam, hasil, p_harap;
    ELSE RAISE NOTICE 'ok %  -> %', f, hasil; END IF;
  END LOOP;
  RETURN gagal;
END $f$;

DO $$
DECLARE g int := 0;
BEGIN
  g := g + pg_temp.uji_nomor('2026-09-30 18:00+00', false, '2610');   -- 1 Okt 01:00 WIB
  g := g + pg_temp.uji_nomor('2026-09-30 10:00+00', false, '2609');   -- kontrol 30 Sep 17:00 WIB
  g := g + pg_temp.uji_nomor('2026-12-31 18:00+00', true,  '2027');   -- 1 Jan 01:00 WIB
  g := g + pg_temp.uji_nomor('2026-12-31 10:00+00', true,  '2026');   -- kontrol
  IF g > 0 THEN RAISE EXCEPTION 'GAGAL C: % panggilan tak mengikuti jam suntik', g; END IF;
  RAISE NOTICE 'LULUS C: 26 fungsi x 2 jam mengikuti tanggal bisnis';
END $$;

ROLLBACK;
\echo GERBANG_T10C_LULUS
