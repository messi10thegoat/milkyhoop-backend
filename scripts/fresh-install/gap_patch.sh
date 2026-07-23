#!/bin/bash
# Gap Patch for milkydb_dryrun — idempotent fixes for schema gaps not covered by v9 migrations
DB="milkydb_dryrun"
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

echo ""
echo "=== GAP PATCH DONE ==="
