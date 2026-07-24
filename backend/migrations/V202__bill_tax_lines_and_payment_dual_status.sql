-- ============================================================================
-- V202__bill_tax_lines_and_payment_dual_status.sql
--
-- Ditemukan golden path langkah 3 (pembelian) di DB murni-resep, lalu
-- diperluas lewat AUDIT SISTEMATIS seluruh backend: setiap daftar kolom
-- `INSERT INTO <tabel> (...)` di 419 file .py di-diff terhadap
-- information_schema. Hasil audit ditriase; migrasi ini HANYA memuat yang
-- terbukti dipakai jalur hidup. Sisanya dicatat sebagai backlog, TIDAK
-- ditambal borongan (banyak di antaranya kode mati — mis. penulis
-- journal_lines.journal_entry_id / journal_entries.posting_date, nama yang
-- justru DILARANG Iron Laws; kanoniknya journal_id / journal_date).
--
-- ---------------------------------------------------------------------------
-- 1. bill_items: tax_rate, tax_amount, dpp
--    Gejala: POST /api/bills/v2 -> 500
--      UndefinedColumnError - column "tax_rate" of relation "bill_items"
--      does not exist
--    Penulis : services/bills_service.py:2717 dan :3881,
--              routers/purchase_orders.py:1342
--    Pembaca : services/bills_service.py:3093 dan :3564
--              ("SELECT id, tax_code_id, tax_rate, tax_amount, dpp FROM bill_items")
--    Artinya faktur pembelian ber-PPN MUSTAHIL dibuat, dan PPN Masukan per
--    baris tak pernah bisa dibukukan.
--    Tipe ditiru dari KEMBARAN AR `sales_invoice_items` (level baris:
--    numeric(5,2) / numeric(18,2)), BUKAN dari header `bills.tax_rate`
--    yang bertipe integer legacy.
-- ---------------------------------------------------------------------------
ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS tax_rate   NUMERIC(5,2)  DEFAULT 0;
ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS tax_amount NUMERIC(18,2) DEFAULT 0;
ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS dpp        NUMERIC(18,2);

COMMENT ON COLUMN bill_items.tax_rate   IS 'Tarif pajak per baris (%). Padanan sales_invoice_items.tax_rate.';
COMMENT ON COLUMN bill_items.tax_amount IS 'Nilai pajak per baris. Disumkan ke bills.tax_amount (bills_service:2749).';
COMMENT ON COLUMN bill_items.dpp        IS 'Dasar Pengenaan Pajak per baris.';

-- ---------------------------------------------------------------------------
-- 2. bill_payments_v2: operational_status, accounting_status
--    Penulis : services/bills_service.py:1594 (record_payment) menulis
--              'CONFIRMED' / 'POSTED' saat INSERT.
--    Pembaca : routers/dashboard.py:2346 ("bp.accounting_status as status").
--    Tanpa kolom ini, PELUNASAN VENDOR gagal 500 dan dashboard hutang pincang.
--
--    AKAR: migrasi dual-status mengenai `bills` dan `receive_payments`
--    (kembaran sisi AR) tapi MELEWATKAN bill_payments_v2. Terbukti asimetris:
--    receive_payments SUDAH punya kedua kolom + CHECK.
--
--    Definisi ditiru PERSIS dari receive_payments — bukan dari `bills`.
--    Alasannya konkret: kode menulis 'CONFIRMED', yang ADA di kosakata
--    receive_payments tapi TIDAK ADA di CHECK milik bills. Menyalin constraint
--    bills justru akan menolak nilai yang sah.
-- ---------------------------------------------------------------------------
ALTER TABLE bill_payments_v2 ADD COLUMN IF NOT EXISTS operational_status VARCHAR(20) DEFAULT 'CREATED';
ALTER TABLE bill_payments_v2 ADD COLUMN IF NOT EXISTS accounting_status  VARCHAR(20) DEFAULT 'UNPOSTED';

DO $c$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid='bill_payments_v2'::regclass
                      AND conname='chk_bpv2_operational_status') THEN
        ALTER TABLE bill_payments_v2 ADD CONSTRAINT chk_bpv2_operational_status
            CHECK (operational_status IN ('CREATED','PENDING_APPROVAL','APPROVED',
                   'SENT_TO_BANK','PROCESSING','CONFIRMED','FAILED','CANCELLED'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid='bill_payments_v2'::regclass
                      AND conname='chk_bpv2_accounting_status') THEN
        ALTER TABLE bill_payments_v2 ADD CONSTRAINT chk_bpv2_accounting_status
            CHECK (accounting_status IN ('UNPOSTED','POSTED','REVERSED'));
    END IF;
END $c$;

-- ---------------------------------------------------------------------------
-- 3. Assertion fail-loud.
-- ---------------------------------------------------------------------------
DO $v202$
DECLARE v_missing TEXT := '';
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name='bill_items' AND column_name='tax_rate')   THEN v_missing := v_missing||' bill_items.tax_rate'; END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name='bill_items' AND column_name='tax_amount') THEN v_missing := v_missing||' bill_items.tax_amount'; END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name='bill_items' AND column_name='dpp')        THEN v_missing := v_missing||' bill_items.dpp'; END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name='bill_payments_v2' AND column_name='operational_status') THEN v_missing := v_missing||' bill_payments_v2.operational_status'; END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name='bill_payments_v2' AND column_name='accounting_status')  THEN v_missing := v_missing||' bill_payments_v2.accounting_status'; END IF;
    IF v_missing <> '' THEN
        RAISE EXCEPTION 'V202: kolom belum terbentuk:%', v_missing;
    END IF;
    -- nilai yang benar-benar ditulis kode harus lolos CHECK
    PERFORM 1 WHERE 'CONFIRMED' IN ('CREATED','PENDING_APPROVAL','APPROVED','SENT_TO_BANK','PROCESSING','CONFIRMED','FAILED','CANCELLED');
    RAISE NOTICE 'V202 OK: bill_items pajak-per-baris + bill_payments_v2 dual-status';
END $v202$;
