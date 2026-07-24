-- ============================================================================
-- V195__restore_hash_chain_and_paygroup_schema.sql
--
-- Memulihkan objek yang DIPANGGIL oleh migrasi lain tapi tidak pernah
-- DIDEFINISIKAN di repo — hilang bersama droplet lama (159.89.197.131).
--
-- Ditemukan 2026-07-24 lewat diff struktural DB-fresh-dari-resep vs milkydb.
-- Arbiter = kode Python + kontrak V145/V188, BUKAN isi milkydb: milkydb
-- terkontaminasi tambalan ad-hoc agen E2E (2026-07-23) yang tidak sah.
--
-- Tanpa migrasi ini, fresh install GAGAL TOTAL: trigger trg_assign_hash_sequence
-- (V145) mereferensi NEW.chain_sequence pada tabel tanpa kolom itu, sehingga
-- SETIAP insert jurnal error "record new has no field chain_sequence".
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. pgcrypto — digest() dipakai compute_journal_hash. Step 0 hanya membuat
--    vector + pg_trgm.
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- 2. Kolom hash-chain di journal_entries.
--    Dipakai: V145 (assign_hash_and_sequence), V188 (verify_chain_integrity).
-- ---------------------------------------------------------------------------
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS chain_sequence BIGINT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS content_hash   VARCHAR(255);
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS previous_hash  VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_je_chain_seq
    ON journal_entries (tenant_id, chain_sequence);

-- ---------------------------------------------------------------------------
-- 3. compute_journal_hash — DEFINISI BARU.
--
--    Definisi asli HILANG (nol migrasi di repo mendefinisikannya; V145 dan
--    V188 hanya memanggil). Karena tidak ada hash historis yang perlu
--    dipertahankan, definisi di bawah menjadi kanonik sejak V195.
--
--    FORMULA (didokumentasikan karena ini definisi baru):
--      sha256(  HEADER || '|LINES|' || LINES || '|' || previous_hash  )
--
--      HEADER = tenant_id | journal_date | journal_number | source_type
--               | total_debit | total_credit
--      LINES  = tiap baris "line_number:account_id:debit:credit:memo"
--               digabung ';' — DIURUTKAN line_number, lalu id sebagai
--               tiebreak. Urutan deterministik WAJIB: verify_chain_integrity
--               menghitung ulang hash dan membandingkannya dengan yang
--               tersimpan; urutan tak stabil = chain gagal acak.
--
--    Semua numerik di-cast ke numeric(18,2) agar representasi teks stabil
--    ('0' vs '0.00' akan menghasilkan hash berbeda).
--
--    CATATAN INTEGRITAS: lines WAJIB ikut. Fungsi tambalan yang sempat
--    terpasang di milkydb hanya menghash tenant_id|journal_date|total_debit,
--    sehingga baris jurnal bisa diubah tanpa mengubah hash — tamper-evidence
--    lumpuh. Jangan pernah mempersempit formula ini ke header saja.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.compute_journal_hash(
    p_journal_id UUID,
    p_prev_hash  VARCHAR
)
RETURNS VARCHAR
LANGUAGE sql
STABLE
AS $function$
    SELECT encode(digest(
        COALESCE((
            SELECT COALESCE(je.tenant_id, '')                        || '|' ||
                   COALESCE(je.journal_date::text, '')               || '|' ||
                   COALESCE(je.journal_number, '')                   || '|' ||
                   COALESCE(je.source_type, '')                      || '|' ||
                   COALESCE(je.total_debit, 0)::numeric(18,2)::text  || '|' ||
                   COALESCE(je.total_credit, 0)::numeric(18,2)::text
              FROM journal_entries je
             WHERE je.id = p_journal_id
        ), '')
        || '|LINES|' ||
        COALESCE((
            SELECT string_agg(
                       COALESCE(jl.line_number::text, '')               || ':' ||
                       COALESCE(jl.account_id::text, '')                || ':' ||
                       COALESCE(jl.debit, 0)::numeric(18,2)::text       || ':' ||
                       COALESCE(jl.credit, 0)::numeric(18,2)::text      || ':' ||
                       COALESCE(jl.memo, ''),
                       ';' ORDER BY jl.line_number NULLS FIRST, jl.id
                   )
              FROM journal_lines jl
             WHERE jl.journal_id = p_journal_id
        ), '')
        || '|' || COALESCE(p_prev_hash, 'GENESIS')
    , 'sha256'), 'hex');
$function$;

COMMENT ON FUNCTION public.compute_journal_hash(UUID, VARCHAR) IS
    'V195: definisi kanonik hash chain jurnal (header + lines + prev_hash). Asli hilang bersama droplet lama. Lines WAJIB ikut demi tamper-evidence.';

-- ---------------------------------------------------------------------------
-- 4. pay_groups — dirujuk routers/pay_groups.py, services/pay_group_access.py,
--    routers/payroll_runs.py, employees.pay_group_id. Nol sumber di repo.
--    Kolom diturunkan dari query kode: id, tenant_id, name, description,
--    is_default, is_active, created_at, updated_at.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pay_groups (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    is_default  BOOLEAN NOT NULL DEFAULT FALSE,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE pay_groups ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
CREATE INDEX IF NOT EXISTS idx_pay_groups_tenant ON pay_groups (tenant_id);

ALTER TABLE pay_groups ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pay_groups_tenant ON pay_groups;
CREATE POLICY pay_groups_tenant ON pay_groups
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ---------------------------------------------------------------------------
-- 5. user_pay_group_access — Layer-3 pay-group filtering.
--    Dirujuk pay_groups.py (GET/PUT /access) + pay_group_access.py.
--    TIDAK ADA di repo MAUPUN di milkydb: E2E tak pernah menyentuhnya karena
--    OWNER/ADMIN selalu bypass. User non-OWNER buka payroll = 500.
--    UNIQUE (user_id, tenant_id, pay_group_id) diwajibkan oleh
--    ON CONFLICT di pay_groups.py.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_pay_group_access (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL,
    tenant_id    TEXT NOT NULL,
    pay_group_id UUID NOT NULL REFERENCES pay_groups(id) ON DELETE CASCADE,
    granted_by   UUID,
    revoked_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_upga_user_tenant_group UNIQUE (user_id, tenant_id, pay_group_id)
);
CREATE INDEX IF NOT EXISTS idx_upga_lookup
    ON user_pay_group_access (user_id, tenant_id) WHERE revoked_at IS NULL;

ALTER TABLE user_pay_group_access ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS upga_tenant ON user_pay_group_access;
CREATE POLICY upga_tenant ON user_pay_group_access
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ---------------------------------------------------------------------------
-- 6. employees — kolom yang TERBUKTI dipakai kode.
--    is_active     : pay_groups.py:42,148 · employees.py:314 · payroll_runs.py:176
--    pay_group_id  : employees.py INSERT + pay_groups.py JOIN
--    email/department/position : employees.py INSERT (kolom eksplisit)
--    SENGAJA TIDAK DIMASUKKAN: basic_salary, salary_type — ditambahkan agen
--    E2E secara ad-hoc, nol referensi di seluruh backend.
-- ---------------------------------------------------------------------------
ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_active    BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS pay_group_id UUID REFERENCES pay_groups(id);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS email        VARCHAR(255);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS department   VARCHAR(255);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS position     VARCHAR(255);

-- ---------------------------------------------------------------------------
-- 7. sales_invoice_items + invoice_fulfillments — kolom yang TERBUKTI ditulis
--    kode. V047 menambah batch_id (bukan batch_no); V022 menambah batch_no ke
--    bill_items, bukan tabel ini. V137 membuat invoice_fulfillments tanpa 2
--    kolom di bawah -> PSAK-72 Event 2 gagal INSERT.
--
--    sales_invoices.py:2075 INSERT INTO sales_invoice_items (... batch_no,
--    exp_date, tax_code_id, dpp)
--    sales_invoices.py:1181,1199 payload_hash (guard idempotensi)
--    sales_invoices.py:1299 revenue_journal_id
--
--    SENGAJA TIDAK DIMASUKKAN: serial_no, uom, conversion_rate — ditambahkan
--    agen E2E secara ad-hoc; nol referensi di SELURUH backend.
-- ---------------------------------------------------------------------------
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS batch_no    VARCHAR(100);
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS exp_date    DATE;
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS tax_code_id UUID;
ALTER TABLE sales_invoice_items ADD COLUMN IF NOT EXISTS dpp         NUMERIC(18,2);

ALTER TABLE invoice_fulfillments ADD COLUMN IF NOT EXISTS payload_hash       TEXT;
ALTER TABLE invoice_fulfillments ADD COLUMN IF NOT EXISTS revenue_journal_id UUID;

-- ---------------------------------------------------------------------------
-- 8. Kolom lain yang TERBUKTI dipakai kode tapi tidak dihasilkan resep.
--
--    METODE: daftar kolom ditentukan oleh ARBITER KODE (grep referensi di
--    routers/ services/ schemas/); TIPE diambil dari struktur milkydb.
--    Yang nol-referensi SENGAJA DIBUANG, mis. tax_codes.coretax_tax_code,
--    employees.basic_salary/salary_type, sales_invoice_items.serial_no/uom/
--    conversion_rate/warehouse_id.
--
--    products.base_unit penting: 188 referensi di backend, tapi V007 (yang
--    membuatnya) TERBUKTI mati saat diuji (ERROR column it.satuan does not
--    exist). Jadi kolomnya dibuat di sini, bukan lewat V007.
--
--    CATATAN customers.name: di milkydb kolom ini NULLABLE karena dilemahkan
--    agen E2E. Resep menghasilkan NOT NULL — versi resep yang BENAR, sengaja
--    tidak diselaraskan ke milkydb.
-- ---------------------------------------------------------------------------
ALTER TABLE bill_items ADD COLUMN IF NOT EXISTS tax_code_id uuid;
ALTER TABLE bill_payments_v2 ADD COLUMN IF NOT EXISTS idempotency_key character varying(255);
ALTER TABLE bill_payments_v2 ADD COLUMN IF NOT EXISTS pph_amount numeric(18,2) DEFAULT 0;
ALTER TABLE bill_payments_v2 ADD COLUMN IF NOT EXISTS pph_tax_code_id uuid;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS tax_code_id uuid;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS alamat text;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS community text;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS company_name character varying(255);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS currency character varying(10) DEFAULT 'IDR'::character varying;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS customer_type character varying(50) DEFAULT 'BADAN'::character varying;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS display_name character varying(255);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_pkp boolean DEFAULT false;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS mobile_phone character varying(50);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS nama character varying(255);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS nik character varying(50);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS nomor_member character varying(100);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS phone2 character varying(50);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS telepon character varying(50);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS website character varying(255);
ALTER TABLE inventory_ledger ADD COLUMN IF NOT EXISTS conversion_factor numeric(18,6);
ALTER TABLE inventory_ledger ADD COLUMN IF NOT EXISTS transaction_quantity numeric(18,4) DEFAULT 0;
ALTER TABLE inventory_ledger ADD COLUMN IF NOT EXISTS transaction_unit character varying(50);
ALTER TABLE journal_lines ADD COLUMN IF NOT EXISTS item_id uuid;
ALTER TABLE products ADD COLUMN IF NOT EXISTS base_unit character varying(50);
ALTER TABLE products ADD COLUMN IF NOT EXISTS deskripsi text;
ALTER TABLE products ADD COLUMN IF NOT EXISTS for_purchases boolean DEFAULT true;
ALTER TABLE products ADD COLUMN IF NOT EXISTS for_sales boolean DEFAULT true;
ALTER TABLE tax_codes ADD COLUMN IF NOT EXISTS coa_id uuid;
ALTER TABLE tax_codes ADD COLUMN IF NOT EXISTS is_withholding boolean DEFAULT false NOT NULL;
ALTER TABLE user_tenant_roles ADD COLUMN IF NOT EXISTS status text DEFAULT 'ACTIVE'::text NOT NULL;

COMMIT;
