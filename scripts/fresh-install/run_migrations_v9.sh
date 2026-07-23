#!/bin/bash
PGDB="${PGDB:-milkydb_dryrun}"
PGUSER="postgres"
MIGDIR="/root/milkyhoop-dev/backend/migrations"
FIXDIR="/tmp/migration_fixes"
LOG="/tmp/migration_results_v9.log"

> "$LOG"
OK=0; FAIL=0; SKIP=0

PG() { docker exec -i milkyhoop-dev-postgres-1 psql -U "$PGUSER" -d "$PGDB" "$@" 2>&1; }

echo "=== STEP 0: Extensions + Prisma base tables ==="
PG << 'PSQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Prisma "Tenant" base table
CREATE TABLE IF NOT EXISTS "Tenant" (
    id TEXT PRIMARY KEY,
    alias TEXT UNIQUE NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    menu_items JSONB NOT NULL DEFAULT '{}',
    address TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Prisma "User" table (must match columns used by V081, V004, V073)
CREATE TABLE IF NOT EXISTS "User" (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    fullname TEXT,
    username TEXT,
    image TEXT,
    "tenantId" TEXT REFERENCES "Tenant"(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Prisma "UserSecurity" table
CREATE TABLE IF NOT EXISTS "UserSecurity" (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    "userId" TEXT REFERENCES "User"(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Stub: outbox (Prisma Outbox) - needed by V002
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    transaksi_id TEXT,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    retry_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_outbox_processed ON outbox(processed, created_at);

-- Stub: transaksi_harian with nama_pihak column (for V017)
CREATE TABLE IF NOT EXISTS transaksi_harian (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    tenant_id TEXT,
    idempotency_key TEXT,
    nama_pihak TEXT,
    tanggal DATE,
    total_amount BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_th_tenant ON transaksi_harian(tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_th_idempotency ON transaksi_harian(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Stub: item_transaksi (Prisma ItemTransaksi @@map("item_transaksi"))
CREATE TABLE IF NOT EXISTS item_transaksi (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    transaksi_id TEXT REFERENCES transaksi_harian(id),
    nama_produk TEXT NOT NULL,
    kategori_path VARCHAR(500),
    level1 VARCHAR(100),
    level2 VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_it_transaksi ON item_transaksi(transaksi_id);

-- Stub: products (Prisma Products @@map("products"))
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    nama_produk VARCHAR(100) NOT NULL,
    satuan VARCHAR(50),
    kategori VARCHAR(100),
    barcode TEXT,
    kode_produk VARCHAR(50),
    harga_jual NUMERIC(18,2),
    harga_beli NUMERIC(18,2),
    stok NUMERIC(18,4) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_products_tenant ON products(tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_tenant_nama ON products(tenant_id, nama_produk);
CREATE INDEX IF NOT EXISTS idx_products_nama_trgm ON products USING GIN (nama_produk gin_trgm_ops);

-- Stub: suppliers (Prisma Supplier @@map("suppliers"))
CREATE TABLE IF NOT EXISTS suppliers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    nama_supplier VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Stub: persediaan (Prisma Persediaan @@map("persediaan"))  
CREATE TABLE IF NOT EXISTS persediaan (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    product_id UUID REFERENCES products(id),
    lokasi_gudang TEXT,
    jumlah NUMERIC(18,4) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Stub: audit_logs (Prisma AuditLog @@map("audit_logs"))
-- Must be created BEFORE V057 which tries CREATE TABLE (will be IF NOT EXISTS)
CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    "userId" TEXT,
    "eventType" TEXT,
    "ipAddress" TEXT,
    "userAgent" TEXT,
    metadata JSONB,
    success BOOLEAN DEFAULT TRUE,
    "errorMessage" TEXT,
    "createdAt" TIMESTAMPTZ DEFAULT NOW(),
    tenant_id TEXT,
    conversation_id TEXT,
    entity_type TEXT,
    entity_id UUID,
    action_id VARCHAR(50),
    source TEXT,
    trace_id UUID,
    entity_number VARCHAR(50),
    input_data JSONB,
    action_plan JSONB,
    validation_result JSONB,
    execution_result JSONB,
    error_code VARCHAR(50),
    duration_ms INTEGER,
    pending_action_id UUID
);

-- Stub: chat_messages (Prisma ChatMessage)
CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    user_id TEXT,
    tenant_id TEXT,
    message TEXT,
    response TEXT,
    metadata JSONB DEFAULT '{}',
    "createdAt" TIMESTAMPTZ DEFAULT NOW(),
    "updatedAt" TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_tenant ON chat_messages(tenant_id);

-- Stub: chat_sessions
CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id UUID,
    summary TEXT,
    title TEXT,
    is_pinned BOOLEAN DEFAULT FALSE,
    pin_reason TEXT,
    pinned_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Stub: chat_session_state
CREATE TABLE IF NOT EXISTS chat_session_state (
    session_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    active_customer_id UUID,
    active_customer_name TEXT,
    active_vendor_id UUID,
    active_vendor_name TEXT,
    active_invoice_id UUID,
    active_invoice_number TEXT,
    active_bill_id UUID,
    active_bill_number TEXT,
    active_items JSONB DEFAULT '[]',
    current_period TEXT,
    current_period_expires_at TIMESTAMPTZ,
    last_action_type TEXT,
    last_action_status TEXT,
    last_action_result JSONB,
    pending_action_id UUID,
    fsm_state TEXT DEFAULT 'IDLE',
    document_context JSONB,
    entity_graph JSONB DEFAULT '{}',
    pending_payload JSONB DEFAULT '{}',
    pending_intent TEXT DEFAULT '',
    editing_mode BOOLEAN DEFAULT FALSE,
    last_domain TEXT,
    last_response_items JSONB,
    active_entity JSONB,
    last_numeric JSONB,
    pending_clarification JSONB,
    pending_clarification_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Stub: employees (payroll)
CREATE TABLE IF NOT EXISTS employees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    employee_code TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- update_updated_at_column generic trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- currencies table (from V041 DDL)
CREATE TABLE IF NOT EXISTS currencies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    code VARCHAR(10) NOT NULL,
    name VARCHAR(100) NOT NULL,
    symbol VARCHAR(10),
    decimal_places INT DEFAULT 2,
    is_base_currency BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_currency_tenant_code UNIQUE(tenant_id, code)
);

-- exchange_rates table (from V041 DDL)
CREATE TABLE IF NOT EXISTS exchange_rates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    base_currency_id UUID REFERENCES currencies(id),
    quote_currency_id UUID REFERENCES currencies(id),
    rate DECIMAL(20,8) NOT NULL DEFAULT 1,
    effective_date DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- products.sales_price and purchase_price (added by V023 which is skipped)
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS sales_price NUMERIC(18,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS purchase_price NUMERIC(18,2) DEFAULT 0;

-- unit_conversions table (created by V023/V194, both skipped; needed by V119+)
CREATE TABLE IF NOT EXISTS unit_conversions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    item_id UUID,
    from_unit VARCHAR(50) NOT NULL,
    to_unit VARCHAR(50) NOT NULL,
    conversion_factor DECIMAL(20,8) NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);


-- payroll_runs table (created by app startup payroll.py; needed by V129 migration)
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
    rejected_at TIMESTAMPTZ,
    rejected_by UUID,
    rejection_reason TEXT,
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    void_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by UUID,
    CONSTRAINT payroll_runs_status_check CHECK (
        status IN ('draft', 'pending_approval', 'approved', 'rejected', 'posted', 'voided')
    )
);
CREATE INDEX IF NOT EXISTS idx_payroll_runs_tenant ON payroll_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_payroll_runs_status ON payroll_runs(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_payroll_runs_period ON payroll_runs(tenant_id, period_start, period_end);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payroll_runs_number ON payroll_runs(tenant_id, payroll_number);

-- payroll_allocations table
CREATE TABLE IF NOT EXISTS payroll_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    payroll_id UUID NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
    employee_id UUID,
    employee_name TEXT NOT NULL,
    employee_code TEXT,
    position TEXT,
    department TEXT,
    basic_salary NUMERIC(18,2) DEFAULT 0,
    allowances JSONB DEFAULT '[]'::jsonb,
    total_allowances NUMERIC(18,2) DEFAULT 0,
    deductions JSONB DEFAULT '[]'::jsonb,
    total_deductions NUMERIC(18,2) DEFAULT 0,
    net_salary NUMERIC(18,2) DEFAULT 0,
    bank_name TEXT,
    bank_account_number TEXT,
    bank_account_name TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payroll_allocations_payroll ON payroll_allocations(payroll_id);
CREATE INDEX IF NOT EXISTS idx_payroll_allocations_tenant ON payroll_allocations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_payroll_allocations_employee ON payroll_allocations(tenant_id, employee_id);

SELECT 'Prerequisites complete' as status;
PSQL

echo "Prerequisites done."

# ============================================================
declare -A SKIP_REASON
SKIP_REASON["V010__accounting_kernel_schema.sql"]="SKIP — partitioned journal_entries with composite PK/FK; superseded by V012+V013"
SKIP_REASON["V011__seed_default_coa.sql"]="SKIP — seed function for V010 schema; V013 provides full replacement"
SKIP_REASON["V012__fix_accounting_tenant_id_type.sql"]="SKIP — alters V010's partitioned chart_of_accounts/journal_entries which are skipped; V013 creates correct non-partitioned tables from scratch"
SKIP_REASON["V074__partition_automation.sql"]="SKIP — journal_entries non-partitioned (V013); partition functions inapplicable"
SKIP_REASON["V128__fix_compute_ar_adjustments_invoice_reversal.sql"]="SKIP — unquoted SQL identifiers (RECEIVABLE, POSTED); function superseded by V170 which ran OK"
SKIP_REASON["V139__rule8_invariant_fix.sql"]="SKIP — unquoted SQL identifiers (POSTED, INVOICE_REVERSAL); function superseded by V169 which ran OK"

# Intermediate step: after V013 adds COA, add missing columns
SKIP_REASON["V006__add_search_gin_indexes.sql"]="SKIP sejarah mati"
SKIP_REASON["V007__add_unit_conversion_fields.sql"]="SKIP sejarah mati"
SKIP_REASON["V020__ap_reconciliation.sql"]="SKIP sejarah mati"
SKIP_REASON["V057__audit_trail.sql"]="SKIP audit_logs from stub"
SKIP_REASON["V101__backfill_dual_status_bill_payments.sql"]="SKIP backfill on fresh DB"
SKIP_REASON["V125__products_tax_indexes.sql"]="SKIP sales_tax_id sejarah mati"
SKIP_REASON["V008__recalculate_persediaan_stock.sql"]="SKIP — references p.base_unit (old column, now satuan), sejarah mati"
SKIP_REASON["V194__create_unit_conversions_item_pricing.sql"]="SKIP — references p.base_unit (old column), sejarah mati"
SKIP_REASON["V041__multi_currency.sql"]="SKIP — currencies+exchange_rates created in Step 0 stub; DML UPDATEs fail on empty fresh DB"
add_coa_columns_if_needed() {
    PG << 'PSQL'
ALTER TABLE chart_of_accounts 
    ADD COLUMN IF NOT EXISTS is_detail BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS is_bank_account BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE roles ADD COLUMN IF NOT EXISTS permissions JSONB DEFAULT '[]';
PSQL
}

run_migration() {
    local file="$1"
    local basename=$(basename "$file")
    
    if [[ -n "${SKIP_REASON[$basename]}" ]]; then
        echo "SKIP | $basename | ${SKIP_REASON[$basename]}" | tee -a "$LOG"

SKIP_REASON["V006__add_search_gin_indexes.sql"]="SKIP — GIN indexes on customers/products; customers not yet created at V006, indexes added later"
SKIP_REASON["V007__add_unit_conversion_fields.sql"]="SKIP — references hpp_per_unit on item_transaksi, old Prisma column, sejarah mati"
SKIP_REASON["V020__ap_reconciliation.sql"]="SKIP — references tenant.nama (old Prisma Tenant schema), sejarah mati"
SKIP_REASON["V057__audit_trail.sql"]="SKIP — audit_logs created in Step 0 stub; V057 adds indexes on event_time/search_text not in stub; stub provides functional audit_logs"
SKIP_REASON["V090__items_advanced_features.sql"]="SKIP-PENDING-FIX — ADD CONSTRAINT IF NOT EXISTS not valid in PG; needs manual fix"
SKIP_REASON["V101__backfill_dual_status_bill_payments.sql"]="SKIP — backfill references old status column that never existed in fresh install"
SKIP_REASON["V125__products_tax_indexes.sql"]="SKIP — indexes on sales_tax_id (column is sales_tax in our schema), sejarah mati"
        ((SKIP++))
        return 0
    fi
    
    local src="$file"
    [[ -f "$FIXDIR/$basename" ]] && src="$FIXDIR/$basename"
    
    ERR=$(docker exec -i milkyhoop-dev-postgres-1 psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 < "$src" 2>&1)
    RC=$?
    
    if [[ $RC -eq 0 ]]; then
        echo "OK   | $basename" | tee -a "$LOG"
        ((OK++))
        # After V013 runs, add missing COA columns before V075 needs them
        if [[ "$basename" == "V013__accounting_kernel_complete.sql" ]]; then
            add_coa_columns_if_needed
            echo "     | [post-V013: added is_detail/is_bank_account/is_system to chart_of_accounts]" | tee -a "$LOG"
        fi
        # After V093 runs (creates roles), add permissions column
        if [[ "$basename" == "V093__access_control_foundation.sql" ]]; then
            PG -c "ALTER TABLE roles ADD COLUMN IF NOT EXISTS permissions JSONB DEFAULT '[]';" > /dev/null 2>&1
            echo "     | [post-V093: added permissions to roles]" | tee -a "$LOG"
        fi
    else
        ERRMSG=$(echo "$ERR" | grep -E "^ERROR|^psql:" | head -2)
        echo "FAIL | $basename | $ERRMSG" | tee -a "$LOG"
        ((FAIL++))
    fi
}

echo ""
echo "=== Running migrations ==="
for f in $(ls "$MIGDIR"/V*.sql | sort -V); do
    run_migration "$f"
done

echo ""
echo "=== SUMMARY ==="
echo "OK: $OK  FAIL: $FAIL  SKIP: $SKIP"
