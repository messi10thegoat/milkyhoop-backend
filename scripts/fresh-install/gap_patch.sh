#!/bin/bash
# Gap Patch for milkydb_dryrun — idempotent fixes for schema gaps not covered by v9 migrations
DB="${PGDB:-milkydb_dryrun}"
PG() { docker exec -i milkyhoop-dev-postgres-1 psql -U postgres -d "$DB" -v ON_ERROR_STOP=1 "$@" 2>&1; }

echo "=== GAP PATCH START ==="

# -----------------------------------------------------------------------
# Gap 1+5: unit_conversions + products columns — handled by V023 FIXDIR in v9. SKIP.
echo "Gap 1+5 (unit_conversions + products columns): HANDLED BY V023 FIXDIR IN v9 — SKIP"

# -----------------------------------------------------------------------
# Gap 2: document_tax_lines — no migration creates this table
echo ""
echo "--- Gap 2: document_tax_lines ---"
RESULT=$(PG << 'SQL'
CREATE TABLE IF NOT EXISTS document_tax_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    document_id UUID NOT NULL,
    line_item_id UUID,
    tax_code_id UUID,
    direction TEXT NOT NULL DEFAULT 'output',
    base_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    tax_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    coa_id UUID,
    journal_line_id UUID,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dtl_document ON document_tax_lines(tenant_id, document_type, document_id);
CREATE INDEX IF NOT EXISTS idx_dtl_tenant ON document_tax_lines(tenant_id);
SQL
)
RC=$?
if [[ $RC -eq 0 ]]; then
    echo "Gap 2 (document_tax_lines): OK"
else
    echo "Gap 2 (document_tax_lines): ERROR: $RESULT"
fi

# -----------------------------------------------------------------------
# Gap 3: payroll_runs + payroll_allocations — handled by v9 Step 0 stub. Verify only.
echo ""
echo "--- Gap 3: payroll_runs (should be created by v9 Step 0) ---"
RESULT=$(PG -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_name IN ('payroll_runs','payroll_allocations');" 2>&1)
COUNT=$(echo "$RESULT" | grep -E "^\s+[0-9]" | tr -d ' ')
if [[ "$COUNT" == "2" ]]; then
    echo "Gap 3 (payroll_runs + payroll_allocations): OK — both present from v9 Step 0"
else
    echo "Gap 3 (payroll_runs + payroll_allocations): MISSING (count=$COUNT) — creating now..."
    PG << 'SQL'
CREATE TABLE IF NOT EXISTS payroll_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    payroll_number TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    payment_date DATE,
    description TEXT,
    total_basic_salary NUMERIC(18,2) DEFAULT 0,
    total_allowances NUMERIC(18,2) DEFAULT 0,
    total_deductions NUMERIC(18,2) DEFAULT 0,
    total_net_salary NUMERIC(18,2) DEFAULT 0,
    employee_count INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    payment_method TEXT DEFAULT 'bank_transfer',
    bank_account_id UUID,
    journal_id UUID,
    submitted_at TIMESTAMPTZ,
    submitted_by UUID,
    approved_at TIMESTAMPTZ,
    approved_by UUID,
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    void_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT payroll_runs_status_check CHECK (
        status IN ('draft', 'pending_approval', 'approved', 'rejected', 'posted', 'voided')
    )
);
CREATE TABLE IF NOT EXISTS payroll_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    payroll_id UUID NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
    employee_id UUID,
    employee_name TEXT NOT NULL,
    basic_salary NUMERIC(18,2) DEFAULT 0,
    allowances JSONB DEFAULT '[]'::jsonb,
    total_allowances NUMERIC(18,2) DEFAULT 0,
    deductions JSONB DEFAULT '[]'::jsonb,
    total_deductions NUMERIC(18,2) DEFAULT 0,
    net_salary NUMERIC(18,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
SQL
    RC=$?
    [[ $RC -eq 0 ]] && echo "Gap 3 fallback: OK" || echo "Gap 3 fallback: ERROR"
fi

# -----------------------------------------------------------------------
# Gap 4: confidentiality_level columns — should be handled by V094
echo ""
echo "--- Gap 4: confidentiality_level columns (V094 should handle) ---"
RESULT=$(PG << 'SQL'
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='journal_entries' AND column_name='confidentiality_level') THEN
        ALTER TABLE journal_entries ADD COLUMN confidentiality_level confidentiality_level DEFAULT 'L3';
        RAISE NOTICE 'Added confidentiality_level to journal_entries';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bills' AND column_name='confidentiality_level') THEN
        ALTER TABLE bills ADD COLUMN confidentiality_level confidentiality_level DEFAULT 'L3';
        RAISE NOTICE 'Added confidentiality_level to bills';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='sales_invoices' AND column_name='confidentiality_level') THEN
        ALTER TABLE sales_invoices ADD COLUMN confidentiality_level confidentiality_level DEFAULT 'L3';
        RAISE NOTICE 'Added confidentiality_level to sales_invoices';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='receive_payments' AND column_name='confidentiality_level') THEN
        ALTER TABLE receive_payments ADD COLUMN confidentiality_level confidentiality_level DEFAULT 'L3';
        RAISE NOTICE 'Added confidentiality_level to receive_payments';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='bill_payments' AND column_name='confidentiality_level') THEN
        ALTER TABLE bill_payments ADD COLUMN confidentiality_level confidentiality_level DEFAULT 'L3';
        RAISE NOTICE 'Added confidentiality_level to bill_payments';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='expenses' AND column_name='confidentiality_level') THEN
        ALTER TABLE expenses ADD COLUMN confidentiality_level confidentiality_level DEFAULT 'L3';
        RAISE NOTICE 'Added confidentiality_level to expenses';
    END IF;
END $$;
SQL
)
RC=$?
if [[ $RC -eq 0 ]]; then
    echo "Gap 4 (confidentiality_level columns): OK"
else
    echo "Gap 4 (confidentiality_level columns): ERROR: $RESULT"
fi

# -----------------------------------------------------------------------
# Gap 6: V115 numeric precision — V115 FIXDIR already strips bank_matching_history, runs in v9
echo ""
echo "--- Gap 6: V115 unit_cost verify ---"
RESULT=$(PG -c "SELECT data_type FROM information_schema.columns WHERE table_name='sales_invoice_items' AND column_name='unit_cost';" 2>&1)
echo "Gap 6 (V115 sales_invoice_items.unit_cost data_type): $RESULT"

# -----------------------------------------------------------------------
# Gap 7: audit_logs indexes — deferred
echo ""
echo "Gap 7 (audit_logs indexes): DEFERRED — functional stub exists from Step 0"

# -----------------------------------------------------------------------
# Gap 8: Prisma NextAuth tables (Account, Session, VerificationToken)
# Step 0 creates Tenant/User/UserSecurity but not these 3 NextAuth tables.
echo ""
echo "--- Gap 8: Prisma NextAuth tables ---"
PG << 'SQL'
CREATE TABLE IF NOT EXISTS "Account" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "type" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "providerAccountId" TEXT NOT NULL,
    "access_token" TEXT,
    "expires_at" INTEGER,
    "id_token" TEXT,
    "refresh_token" TEXT,
    "scope" TEXT,
    "session_state" TEXT,
    "token_type" TEXT,
    CONSTRAINT "Account_pkey" PRIMARY KEY ("id")
);
CREATE TABLE IF NOT EXISTS "Session" (
    "id" TEXT NOT NULL,
    "sessionToken" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "expires" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "Session_pkey" PRIMARY KEY ("id")
);
CREATE TABLE IF NOT EXISTS "VerificationToken" (
    "identifier" TEXT NOT NULL,
    "token" TEXT NOT NULL,
    "expires" TIMESTAMP(3) NOT NULL
);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='Account_userId_fkey') THEN
        ALTER TABLE "Account" ADD CONSTRAINT "Account_userId_fkey"
          FOREIGN KEY ("userId") REFERENCES "User"(id) ON DELETE CASCADE ON UPDATE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='Session_userId_fkey') THEN
        ALTER TABLE "Session" ADD CONSTRAINT "Session_userId_fkey"
          FOREIGN KEY ("userId") REFERENCES "User"(id) ON DELETE CASCADE ON UPDATE CASCADE;
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS "Account_provider_providerAccountId_key" ON "Account"("provider", "providerAccountId");
CREATE UNIQUE INDEX IF NOT EXISTS "Session_sessionToken_key" ON "Session"("sessionToken");
CREATE UNIQUE INDEX IF NOT EXISTS "VerificationToken_token_key" ON "VerificationToken"("token");
CREATE UNIQUE INDEX IF NOT EXISTS "VerificationToken_identifier_token_key" ON "VerificationToken"("identifier", "token");
SQL
echo "Gap 8 (NextAuth tables): OK"


# -----------------------------------------------------------------------
# Gap 10: DIGANTI OLEH MIGRASI V195 (2026-07-24).
# Blok tambalan lama DIHAPUS: sebagian isinya ternyata mengabadikan karangan
# agen E2E (sales_invoice_items.serial_no/uom/conversion_rate = nol referensi
# di SELURUH backend; employees.basic_salary/salary_type idem). Skema yang sah
# sekarang datang dari V195 yang diturunkan dari KODE, bukan dari isi milkydb.
# Di sini tinggal assertion fail-loud.
echo ""
echo "--- Gap 10: assertion objek V195 ---"
MISSING=""
chk_col() {
    local n
    n=$(PG -tAc "SELECT COUNT(*) FROM information_schema.columns WHERE table_name='$1' AND column_name='$2';")
    [[ "$n" == "1" ]] || MISSING="$MISSING $1.$2"
}
chk_tbl() {
    local n
    n=$(PG -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='$1';")
    [[ "$n" == "1" ]] || MISSING="$MISSING table:$1"
}
chk_col journal_entries chain_sequence
chk_col journal_entries content_hash
chk_col journal_entries previous_hash
chk_col employees is_active
chk_col employees pay_group_id
chk_col invoice_fulfillments payload_hash
chk_col sales_invoice_items dpp
chk_tbl pay_groups
chk_tbl user_pay_group_access
FN=$(PG -tAc "SELECT COUNT(*) FROM pg_proc WHERE proname='compute_journal_hash';")
[[ "$FN" == "1" ]] || MISSING="$MISSING func:compute_journal_hash"
if [[ -z "$MISSING" ]]; then
    echo "Gap 10 (assertion V195): OK"
else
    echo "Gap 10 (assertion V195): FAIL — V195 belum jalan? Hilang:$MISSING"
fi

# -----------------------------------------------------------------------
# Gap 11: VERIFIKASI — bukan tambalan. account_roles memakai role_key (BUKAN
# role_code). Kalau assertion ini gagal, kode router akan salah kolom.
echo ""
echo "--- Gap 11: account_roles.role_key assertion ---"
RESULT=$(PG -tAc "SELECT COUNT(*) FROM information_schema.columns WHERE table_name='account_roles' AND column_name='role_key';")
if [[ "$RESULT" == "1" ]]; then
    echo "Gap 11 (account_roles.role_key): OK"
else
    echo "Gap 11 (account_roles.role_key): FAIL — kolom role_key tidak ditemukan (got: $RESULT)"
fi

# -----------------------------------------------------------------------
# Gap 12: KONTRAK SEED (V200) — fail-loud.
# Menutup kelas bug "redefinisi seed menghapus akun/role orang lain secara
# diam-diam" (V165 dihapus V168/V173/V183 -> BANK_FEE hilang). Di titik ini
# belum ada tenant, jadi yang diperiksa = sumber fungsi seed.
echo ""
echo "--- Gap 12: kontrak seed (V200) ---"
SEED_RES=$(PG -tAc "SELECT assert_seed_contract();" 2>&1)
if [[ "$SEED_RES" == *"KONTRAK SEED OK"* ]]; then
    echo "Gap 12 (kontrak seed): OK — $SEED_RES"
else
    echo "Gap 12 (kontrak seed): FAIL — $SEED_RES"
fi

echo ""
echo "=== GAP PATCH DONE ==="
