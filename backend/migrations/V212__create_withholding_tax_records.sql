-- ============================================================================
-- V212__create_withholding_tax_records.sql
--
-- BUG: GET /api/tax-reports/pph?period=YYYY-MM → 500
--      asyncpg UndefinedTableError: relation "withholding_tax_records" does not exist.
--
-- KELAS: TABEL HILANG (bukan drift kolom). Tabel ini dirujuk 5 file kode
-- (bills_service, bill_payments, expenses, payroll_runs = 4 INSERT + 1 UPDATE
-- void; tax_reports = 2 SELECT) tapi TAK PERNAH didefinisikan migrasi manapun
-- maupun ada di dump manapun (e2egreen/tarball/git mirror) → tabel ad-hoc
-- runtime lama yang tak pernah tertangkap resep. ARBITER = KODE.
--
-- Sifat: SIDECAR METADATA (bukan source of truth). Angka PPh di laporan
-- diturunkan dari journal_lines (Law 1/29); tabel ini hanya meng-ENRICH baris
-- laporan (npwp vendor, tax_code, dpp). Maka: minimal, NULLable longgar, TANPA
-- CHECK/FK — hindari jebakan over-constraint (kelas V197/V205 false-reject).
--
-- Union kolom yang DIRUJUK kode (semua INSERT+UPDATE+SELECT):
--   id, tenant_id, direction('cut'), tax_code_id, document_type
--   (BILL_PAYMENT|EXPENSE|PAYROLL), document_id, payment_id (hanya path bill),
--   journal_id, vendor_id, npwp, tax_period, base_amount, tax_amount,
--   status('recorded'|'void'), updated_at (di-SET path void).
--   → payroll_runs INSERT TIDAK menyertakan id ⇒ id WAJIB punya DEFAULT.
--   → tenant_id TEXT (konsisten journal_entries/bills/expenses/tax_codes).
--
-- CATATAN (bug fungsional TERPISAH, di luar scope pembuatan tabel): format
-- tax_period tidak konsisten antar modul — bill paths tulis 'YYYYMM'
-- (strftime %Y%m) sedangkan payroll/expense tulis 'YYYY-MM'. Cross-check di
-- tax_reports memfilter tax_period = period ('YYYY-MM') → total silang bisa
-- meleset utk data bill. Tabel ini menyimpan apa adanya (TEXT); normalisasi
-- format = perbaikan kode terpisah.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS withholding_tax_records (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT NOT NULL,
    direction     TEXT NOT NULL DEFAULT 'cut',      -- 'cut' (dipotong) | 'collect'
    tax_code_id   UUID,                             -- ref tax_codes(id), LEFT JOIN (nullable)
    document_type TEXT NOT NULL,                    -- BILL_PAYMENT | EXPENSE | PAYROLL
    document_id   UUID,
    payment_id    UUID,                             -- hanya path bill_payment; kunci UPDATE void
    journal_id    UUID,                             -- ref journal_entries(id), kunci enrich SELECT
    vendor_id     UUID,                             -- ref vendors(id), LEFT JOIN (nullable, payroll NULL)
    npwp          TEXT,
    tax_period    TEXT,                             -- 'YYYYMM' | 'YYYY-MM' (lihat catatan di atas)
    base_amount   NUMERIC(18,2) NOT NULL DEFAULT 0, -- DPP (Law 25)
    tax_amount    NUMERIC(18,2) NOT NULL DEFAULT 0, -- PPh (Law 25)
    status        TEXT NOT NULL DEFAULT 'recorded', -- 'recorded' | 'void'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index mengikuti PERSIS pola query kode:
--   enrich SELECT : WHERE tenant_id=$1 AND journal_id = ANY($2) AND status='recorded'
--   cross-check   : WHERE tenant_id=$1 AND tax_period=$2 AND status='recorded' AND direction='cut'
--   void UPDATE   : WHERE payment_id=$1 AND tenant_id=$2 AND status!='void'
CREATE INDEX IF NOT EXISTS idx_wtr_tenant_journal ON withholding_tax_records (tenant_id, journal_id);
CREATE INDEX IF NOT EXISTS idx_wtr_tenant_period  ON withholding_tax_records (tenant_id, tax_period);
CREATE INDEX IF NOT EXISTS idx_wtr_payment        ON withholding_tax_records (payment_id) WHERE payment_id IS NOT NULL;

DO $v212$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_schema='public' AND table_name='withholding_tax_records') THEN
        RAISE EXCEPTION 'V212: tabel withholding_tax_records belum terbentuk';
    END IF;
    RAISE NOTICE 'V212 OK: withholding_tax_records dibuat (sidecar metadata PPh, 16 kolom + 3 index)';
END $v212$;

COMMIT;
